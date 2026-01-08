import streamlit as st
from streamlit_image_comparison import image_comparison

import os
from pathlib import Path
import glob
import cv2
import tqdm
import time
import tempfile
import shutil
from PIL import ImageColor, Image
import zipfile
import io
import fitz
import concurrent.futures

# Global variables (retained for compatibility with original structure)
before_file_dict = {}
after_file_dict = {}

# Helper function for pdf2images to process one page in parallel
def convert_page_to_image(args):
    """Converts a single PDF page to a JPG image."""
    pdf_file_path, output_dir, page_num, change_scale, pdf_filename = args
    try:
        doc = fitz.open(pdf_file_path)
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=200)

        if change_scale == "GRAY":
            pix = fitz.Pixmap(fitz.csGRAY, pix)

        file_name = output_dir / f"{pdf_filename}_{page_num:004d}.jpg"
        pix.save(str(file_name))
        return True
    except Exception as e:
        st.error(f"Error converting page {page_num} of {pdf_filename}: {e}")
        return False

def pdf2images(k, pdf_path, bar, base_num, change_scale):
    """Converts all PDFs in a directory to images in parallel."""
    pdfs = glob.glob(str(pdf_path / "*.pdf"), recursive=False)
    if not pdfs:
        st.error("No PDF files found in the specified directory.")
        return bar

    if k == 0:
        output_dir = pdf_path / "before_pdf_img"
        print_text = "突き合わせ元"
    else:  # k == 1
        output_dir = pdf_path / "after_pdf_img"
        print_text = "突き合わせ先"
    
    output_dir.mkdir(exist_ok=True)

    tasks = []
    total_pages = 0
    for pdf in pdfs:
        try:
            doc = fitz.open(pdf)
            pdf_filename, _ = os.path.splitext(os.path.basename(pdf))
            total_pages += len(doc)
            for i in range(len(doc)):
                tasks.append((pdf, output_dir, i, change_scale, pdf_filename))
        except Exception as e:
            st.error(f"Could not open {pdf}: {e}")

    if total_pages == 0:
        return bar

    bar_increment = 30 / total_pages
    bar_num = base_num
    
    print(f"-----{print_text}のPDFをjpegに変換中-----")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Using tqdm for console progress, st.progress for UI
        future_to_task = {executor.submit(convert_page_to_image, task): task for task in tasks}
        for future in tqdm.tqdm(concurrent.futures.as_completed(future_to_task), total=len(tasks), desc=f"Converting {print_text}"):
            if future.result():
                bar_num += bar_increment
                bar.progress(int(min(bar_num, base_num + 30)), text="PDFをJPEGに変換中...")

    print("------完了！------")
    return bar

# Helper function for find_diff to process one image pair in parallel
def compare_images(args):
    """Compares two images and returns the difference status and result path."""
    before_jpg_file, after_jpg_file, result_folder, color, bold, index = args
    
    _, a_filename = os.path.split(os.path.splitext(after_jpg_file)[0])
    
    img_ref = cv2.imread(before_jpg_file)
    img_comp = cv2.imread(after_jpg_file)
    if img_ref is None or img_comp is None:
        return "Error: Could not read image", None, index

    # --- ### サイズを統一する処理を追加 ### ---
    # img_ref のサイズに img_comp を強制的に合わせます
    if img_ref.shape != img_comp.shape:
        # img_ref.shape は (高さ, 幅, チャンネル数)
        height, width = img_ref.shape[:2]
        img_comp = cv2.resize(img_comp, (width, height))
    # ------------------------------------------

    temp = img_comp.copy()

    gray_img_ref = cv2.cvtColor(img_ref, cv2.COLOR_BGR2GRAY)
    gray_img_comp = cv2.cvtColor(img_comp, cv2.COLOR_BGR2GRAY)

    img_diff = cv2.absdiff(gray_img_ref, gray_img_comp)
    _, img_bin = cv2.threshold(img_diff, 50, 255, 0)
    img_bin = cv2.bitwise_and(img_bin, cv2.cvtColor(img_ref, cv2.COLOR_BGR2GRAY))

    contours, _ = cv2.findContours(img_bin, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    has_diff = "There are differences" if any(cv2.boundingRect(c)[2] > 5 and cv2.boundingRect(c)[3] > 5 for c in contours) else ""
    
    if has_diff:
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if width > 5 or height > 5:
                cv2.rectangle(temp, (x - 2, y - 2), (x + width + 2, y + height + 2), color, bold)

    result_path = str(result_folder / (a_filename + ".jpg"))
    cv2.imwrite(result_path, temp)
    
    return has_diff, result_path, index

def find_diff(before_pdf_path, after_pdf_path, color, bold, bar):
    """Finds differences between all corresponding images in parallel."""
    before_jpg_files = sorted(glob.glob(str(before_pdf_path / "before_pdf_img/*.jpg")))
    after_jpg_files = sorted(glob.glob(str(after_pdf_path / "after_pdf_img/*.jpg")))

    if not before_jpg_files or not after_jpg_files:
        st.error("JPEGファイルが見つかりません。")
        return "error", [], bar
    
    if len(before_jpg_files) != len(after_jpg_files):
        st.error("元ファイルと先ファイルのページ数が一致しません。")
        return "error", [], bar

    result_folder = after_pdf_path / "result_folder"
    result_folder.mkdir(exist_ok=True)

    tasks = [(before_jpg_files[j], after_jpg_files[j], result_folder, color, bold, j) for j in range(len(before_jpg_files))]
    
    bar_increment = 30 / len(before_jpg_files)
    bar_num = 70
    
    differences = [None] * len(before_jpg_files)
    
    print("------突き合わせ元と突き合わせ先の差分を集約中------")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_index = {executor.submit(compare_images, task): task[-1] for task in tasks}
        
        for future in tqdm.tqdm(concurrent.futures.as_completed(future_to_index), total=len(tasks), desc="差分を検出中"):
            index = future_to_index[future]
            try:
                has_diff, _, _ = future.result()
                differences[index] = has_diff
            except Exception as exc:
                print(f'タスク {index} で例外が発生しました: {exc}')
                differences[index] = "エラー"

            bar_num += bar_increment
            bar.progress(int(min(bar_num, 100)), text="差分を検出中...")

    print("------完了!------")
    
    return result_folder, differences, bar

def make_check_filekey(key_file):
    root, ext = os.path.splitext(key_file)
    dirname, filename = os.path.split(root)
    filename_del = filename.split("_")
    filekey = filename_del[0]
    return filekey

def streamlit_main():
    st.title(":hammer_and_wrench: pdf difference checker :hammer_and_wrench:")
    st.divider()

    st.sidebar.title("Upload")
    before_pdf_file = st.sidebar.file_uploader("突き合わせ元のpdf", accept_multiple_files=True, type="pdf")
    st.sidebar.title("")
    after_pdf_file = st.sidebar.file_uploader("突き合わせ先のpdf", accept_multiple_files=True, type="pdf")
    st.sidebar.divider()
    st.sidebar.title("Options")
    change_scale = st.sidebar.selectbox(
        "差分チェックをするスケール",
        ("RGB", "GRAY")
    )
    color = st.sidebar.color_picker("マーキングする色", "#00ff00")
    bold = st.sidebar.slider(
        "差分を囲う線の太さ", 0, 10, 3)
    st.sidebar.divider()

    if not before_pdf_file or not after_pdf_file:
        st.warning("突き合わせ元と突き合わせ先のpdfファイルのページ数と縦横のサイズが同じことを確認の上，アップロードをしてください。", icon="⚠️")
        st.warning("色の差分チェックは苦手です。ご了承ください。", icon="⚠️")
    else:
        if st.button("突き合わせ開始"):
            before_temp_dir, after_temp_dir = None, None
            try:
                success = st.empty()
                success.success("ファイルアップロード成功!")
                color_rgb = ImageColor.getcolor(color, "RGB")
                color_bgr = (color_rgb[2], color_rgb[1], color_rgb[0])
                time.sleep(1)
                success.empty()

                if len(before_pdf_file) != len(after_pdf_file):
                    st.error("アップロードされているファイルの数が等しくありません。ご確認ください。", icon="🚨")
                    return

                bar = st.progress(0, text="PDFファイルを読み込み中...")
                
                # Create temporary directories
                before_temp_dir = Path(tempfile.mkdtemp())
                after_temp_dir = Path(tempfile.mkdtemp())

                # Save uploaded files to temp dirs
                for i, b_pdf in enumerate(before_pdf_file):
                    path = before_temp_dir / f"{i:003}.pdf"
                    before_file_dict[f"{i:003}"] = Path(b_pdf.name).stem
                    with open(path, "wb") as f:
                        f.write(b_pdf.getbuffer())
                
                for i, a_pdf in enumerate(after_pdf_file):
                    path = after_temp_dir / f"{i:003}.pdf"
                    after_file_dict[f"{i:003}"] = Path(a_pdf.name).stem
                    with open(path, "wb") as f:
                        f.write(a_pdf.getbuffer())

                bar.progress(10, text="PDFをJPEGに変換中...")
                bar = pdf2images(0, before_temp_dir, bar, 10, change_scale)
                
                bar.progress(40, text="PDFをJPEGに変換中...")
                bar = pdf2images(1, after_temp_dir, bar, 40, change_scale)
                
                bar.progress(70, text="差分を検出中...")
                result_folder, differences, bar = find_diff(before_temp_dir, after_temp_dir, color_bgr, bold, bar)

                if result_folder == "error":
                    return

                before_jpg_files = sorted(glob.glob(str(before_temp_dir / "before_pdf_img/*.jpg")))
                result_jpgs = sorted(glob.glob(str(result_folder / "*.jpg")))
                
                # Ensure all result images are written before proceeding
                while len(result_jpgs) != len(before_jpg_files):
                    time.sleep(0.5)
                    result_jpgs = sorted(glob.glob(str(result_folder / "*.jpg")))

                bar.progress(100, text="完了!")
                time.sleep(0.5)
                bar.empty()
                
                zip_io = io.BytesIO()
                with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zip_file:
                    st.toast("結果を表示中…", icon="🏃‍♂️")
                    old_a_file_key = ""
                    for i in range(len(result_jpgs)):
                        before_file_key = make_check_filekey(before_jpg_files[i])
                        after_file_key = make_check_filekey(result_jpgs[i]) # Use result file for key

                        if old_a_file_key != str(after_file_dict.get(after_file_key, '')):
                            if old_a_file_key != "":
                                st.divider()

                        if i < len(differences) and differences[i]:
                            st.header(f":bell: :red[{differences[i]}]")

                        image_comparison(
                            img1=Image.open(before_jpg_files[i]),
                            img2=Image.open(result_jpgs[i]),
                            label1=before_file_dict.get(before_file_key, "元画像"),
                            label2=after_file_dict.get(after_file_key, "比較画像"),
                            width=700,
                            starting_position=1
                        )

                        with open(result_jpgs[i], "rb") as img_file:
                            zip_file.writestr(f"result_{after_file_dict.get(after_file_key, i)}_{i:003d}.jpg", img_file.read())
                        
                        old_a_file_key = str(after_file_dict.get(after_file_key, ''))

                st.divider()
                zip_io.seek(0)
                st.download_button(
                    label="差分画像をZIPでダウンロード",
                    data=zip_io,
                    file_name="result.zip",
                    mime="application/zip"
                )

                st.balloons()
                st.toast('全ての表示が完了しました！', icon='😍')

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
            finally:
                # Cleanup temp directories
                if before_temp_dir and os.path.exists(before_temp_dir):
                    shutil.rmtree(before_temp_dir)
                if after_temp_dir and os.path.exists(after_temp_dir):
                    shutil.rmtree(after_temp_dir)

def main():
    st.set_page_config(
        page_title="Pdf Difference Checker",
        page_icon=":file_cabinet:",
        initial_sidebar_state="expanded"
    )
    streamlit_main()

if __name__ == "__main__":
    main()

