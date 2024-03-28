import streamlit as st
from streamlit_image_comparison import image_comparison

import os
from pathlib import Path
from pdf2image import convert_from_path
import glob
import cv2
import tqdm
import time
import tempfile
import shutil
from PIL import ImageColor
import zipfile
import io


before_file_dict = {}; after_file_dict = {}; difference = []; diff_link = []; diff_link_name = [];
def add_poppler_path():
    os.environ['PATH'] = "/mount/src/pdf_difference_checker/poppler/Library/bin"
    print(os.environ["PATH"]) 
    
def pdf2images(k, pdf_path):
    pdfs = glob.glob(pdf_path + r"/*.pdf", recursive = False)
    if k == 0:
        output_dir = Path(r"{}/before_pdf_img".format(pdf_path))
        print_text = "突き合わせ元"
    elif k == 1:
        output_dir = Path(r"{}/after_pdf_img".format(pdf_path))
        print_text = "突き合わせ先"
    else:
        pass
    output_dir.mkdir(exist_ok = True)
    for pdf in pdfs:
        root, ext = os.path.splitext(pdf)
        dirname, filename = os.path.split(root)
        pages = convert_from_path(str(pdf) , dpi = 200)
        print()
        print("-----{}の{}つ目のPDFをjpegに変換中-----".format(print_text, int(filename.split("_")[2]) + 1))
        for i, page in tqdm.tqdm(enumerate(pages)):
            file_name = output_dir / "{}_{:004d}.jpg".format(filename, i + 1)
            page.save(str(file_name), "JPEG")
        print("------完了！------")
        print()


def find_diff(before_pdf_path, after_pdf_path, color, bold):
    before_jpg_files = glob.glob(before_pdf_path + r"/before_pdf_img/*.jpg", recursive = False)
    after_jpg_files = glob.glob(after_pdf_path + r"/after_pdf_img/*.jpg", recursive = False)
    
    result_folder = Path(after_pdf_path + r"/result_folder")
    result_folder.mkdir(exist_ok = True)

    #try:
    print("------突き合わせ元と突き合わせ先の差分を集約中------")
    for j in tqdm.tqdm(range(0, len(before_jpg_files))):
        root, ext = os.path.splitext(after_jpg_files[j])
        a_dirname, a_filename = os.path.split(root)
        img_ref = cv2.imread(before_jpg_files[j])
        img_comp = cv2.imread(after_jpg_files[j])
        temp = img_comp.copy()

        gray_img_ref = cv2.cvtColor(img_ref, cv2.COLOR_BGR2GRAY)
        gray_img_comp = cv2.cvtColor(img_comp, cv2.COLOR_BGR2GRAY)

        img_diff = cv2.absdiff(gray_img_ref, gray_img_comp)

        ret, img_bin = cv2.threshold(img_diff, 50, 255, 0)

        img_bin = cv2.bitwise_and(img_bin, cv2.cvtColor(img_ref, cv2.COLOR_BGR2GRAY))

        # 輪郭検出を行う
        contours, hierarchy = cv2.findContours(img_bin, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if len(contours) != 0:
            difference.append("There are differences")
        else:
            difference.append("")
        # 輪郭に印をつける
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if width > 5 or height > 5:
                cv2.rectangle(temp, (x-2, y-2), (x + width + 2, y + height + 2), color, bold)
            else:
                continue
        cv2.imwrite(str(result_folder / ("result_" + a_filename + ".jpg")), temp)
    print("------完了!------")

    return result_folder, difference

    #except:
    #    st.error("""
    #    エラーが発生しました。
    #    pdfのサイズが正しいか，ページ数が正しいか等ご確認ください。
    #    """, icon="🚨")
    #    return "error" , ""
        
def make_check_filekey(key_file):
    root, ext = os.path.splitext(key_file)
    dirname, filename = os.path.split(root)
    filename_del = filename.split("_")
    filekey = "_".join(filename_del[:3])
    return filekey

def streamlit_main():
    st.title(":hammer_and_wrench: pdf difference checker :hammer_and_wrench:")
    st.divider()

    st.sidebar.title("Upload")
    before_pdf_file = st.sidebar.file_uploader("突き合わせ元のpdf", accept_multiple_files=True, type = "pdf")
    st.sidebar.title("")
    after_pdf_file = st.sidebar.file_uploader("突き合わせ先のpdf", accept_multiple_files=True, type = "pdf")
    st.sidebar.divider()
    st.sidebar.title("Options")
    color = st.sidebar.color_picker("マーキングする色", "#00ff00")
    bold = st.sidebar.slider(
        "差分を囲う線の太さ", 0, 10, 3)
    st.sidebar.divider()
    
    if len(before_pdf_file) == 0 or len(after_pdf_file) == 0:
        st.warning("突き合わせ元と突き合わせ先のpdfファイルのページ数と縦横のサイズが同じことを確認の上，アップロードをしてください。", icon="⚠️")
        st.warning("色の差分チェックは苦手です。ご了承ください。", icon="⚠️")
    else:
        if st.button("突き合わせ開始"):
            try:
                success = st.empty()
                top_list = st.empty()
                success.success("File Upload Successfully!")
                color = ImageColor.getcolor(color, "RGB")
                color = (color[2], color[1], color[0])
                time.sleep(1)
                success.empty()
                print("color:{}".format(color))
                if len(before_pdf_file) == len(after_pdf_file):
                    print("success len")
                    num = 0
                    bar = st.progress(0, text="Loading PDF File...")
                    before_temp_dir = tempfile.mkdtemp()
                    print("before_temp_dir:{}".format(before_temp_dir))
                    for l, b_pdf_file in enumerate(before_pdf_file):
                        before_pdf_path_temp = os.path.join(before_temp_dir, f"before_pdf_{l}.pdf")
                        before_file_dict[f"before_pdf_{l}"] = str(b_pdf_file.name.replace(".pdf", ""))
                        print("before_pdf_path_temp:{}".format(before_pdf_path_temp))
                        with open(before_pdf_path_temp, "wb") as out:
                            out.write(b_pdf_file.getbuffer())
                            print(out)
                    
                    after_temp_dir = tempfile.mkdtemp()
                    print("after_temp_dir:{}".format(after_temp_dir))
                    for m, a_pdf_file in enumerate(after_pdf_file):
                        after_pdf_path_temp = os.path.join(after_temp_dir, f"after_pdf_{m}.pdf")
                        after_file_dict[f"after_pdf_{m}"] = str(a_pdf_file.name.replace(".pdf", ""))
                        print("after_pdf_path_temp:{}".format(after_pdf_path_temp))
                        with open(after_pdf_path_temp, "wb") as out:
                            out.write(a_pdf_file.getbuffer())
                            print(out)
                    
                    bar = bar.progress(10, text="Converting the PDF to JPEG...")
                    pdf2images(0, before_temp_dir)
                    time.sleep(1)
                    bar = bar.progress(40, text="Converting the PDF to JPEG...")
                    pdf2images(1, after_temp_dir)
                    time.sleep(1)
                    bar = bar.progress(70, text="Converting the PDF to JPEG...")
                    result_folder, difference = find_diff(before_temp_dir, after_temp_dir, color, bold)
                    if result_folder == "error":
                        pass
                    else:
                        before_jpg_files = glob.glob(before_temp_dir + r"/before_pdf_img/*.jpg", recursive = False)
                        after_jpg_files = glob.glob(after_temp_dir + r"/after_pdf_img/*.jpg", recursive = False)
                        while True:
                            result_jpgs = glob.glob(os.path.join(result_folder, "*.jpg"), recursive=False)
                            if len(result_jpgs) != len(after_jpg_files):
                                time.sleep(1)
                            else:
                                break
                        bar = bar.progress(100, text="Done!")
                        time.sleep(.5)
                        bar = bar.empty()
                        old_a_file_key = ""
                        zip_io = io.BytesIO()
                        #diff_num = 1
                        with zipfile.ZipFile(zip_io, "w") as zip_file:
                            st.toast("表示中…", icon = "🏃‍♂️")
                            for i in range(0, len(result_jpgs)):
                                before_file_key = make_check_filekey(before_jpg_files[i])
                                after_file_key = make_check_filekey(after_jpg_files[i])

                                if old_a_file_key != str(after_file_dict[after_file_key]):
                                    if old_a_file_key != "":
                                        st.divider()
                                    
                                if difference[i] == "":
                                    st.write("")
                                else:
                                    st.header(":bell: :red[{}]".format(difference[i]))
                                    #st.header(":red[{}_No.{}]".format(difference[i], diff_num))
                                    #diff_num += 1
                                    #diff_link.append("#difference-{}".format(diff_num))
                                    #diff_link_name.append("{}枚目:{}".format(i, after_file_dict[after_file_key]))
                                
                                image_comparison(
                                    img1 = before_jpg_files[i],
                                    img2 = result_jpgs[i],
                                    label1 = before_file_dict[before_file_key],
                                    label2 = after_file_dict[after_file_key],
                                    width = 700,
                                    starting_position = 1
                                )
                                
                                with open(result_jpgs[i], "rb") as img_file:
                                    zip_file.writestr("result_{}_{:003d}.jpg".format(after_file_dict[after_file_key], i), img_file.read())

                                old_a_file_key = str(after_file_dict[after_file_key])

                        st.divider()

                        zip_io.seek(0)
                        st.download_button(
                            label="Download Zip file",
                            data=zip_io,
                            file_name="result.zip",
                            mime="application/zip"
                        )

                        st.balloons()
                        st.toast('全ての表示が完了しました！', icon='😍')
                        shutil.rmtree(before_temp_dir)
                        shutil.rmtree(after_temp_dir)

                else:
                    st.error("アップロードされているファイルの数が等しくありません。ご確認ください。", icon="🚨")

            except Exception as e:
                print(e)
                try :
                    shutil.rmtree(before_temp_dir)
                    shutil.rmtree(after_temp_dir)
                except:
                    pass

def main():
    st.set_page_config(
        page_title = "Pdf Difference Checker",
        page_icon = ":file_cabinet:",
        initial_sidebar_state="expanded"
    )
    add_poppler_path()
    streamlit_main()

if __name__ == "__main__":
    main()
