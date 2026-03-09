import streamlit as st
from streamlit_image_comparison import image_comparison

from pathlib import Path
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


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def contour_is_significant(contour, min_width: int = 5, min_height: int = 5) -> bool:
    x, y, w, h = cv2.boundingRect(contour)
    return w > min_width and h > min_height


def build_pdf_manifest(uploaded_files, base_dir: Path):
    """
    UploadされたPDFを保存し、PDF単位のmanifestを返す。
    uploaded_files の順序をそのまま保持する。
    """
    manifest = []

    for i, uploaded_pdf in enumerate(uploaded_files):
        pdf_index = f"{i:03d}"
        saved_path = base_dir / f"{pdf_index}.pdf"

        with open(saved_path, "wb") as f:
            f.write(uploaded_pdf.getbuffer())

        manifest.append(
            {
                "pdf_index": pdf_index,
                "display_name": Path(uploaded_pdf.name).stem,
                "pdf_path": saved_path,
                "upload_order": i,
            }
        )

    return manifest


def convert_single_pdf_to_images(args):
    """
    1つのPDFを全ページ画像化する。
    スレッド内では Streamlit UI を触らず、結果だけ返す。
    """
    pdf_info, output_dir, change_scale = args

    pdf_index = pdf_info["pdf_index"]
    pdf_path = pdf_info["pdf_path"]
    display_name = pdf_info["display_name"]
    upload_order = pdf_info["upload_order"]

    page_manifest = []
    errors = []

    try:
        with fitz.open(pdf_path) as doc:
            page_count = len(doc)

            for page_num in range(page_count):
                try:
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=200)

                    if change_scale == "GRAY":
                        pix = fitz.Pixmap(fitz.csGRAY, pix)

                    image_filename = f"{pdf_index}_{page_num:04d}.jpg"
                    image_path = output_dir / image_filename
                    pix.save(str(image_path))

                    page_manifest.append(
                        {
                            "pdf_index": pdf_index,
                            "display_name": display_name,
                            "upload_order": upload_order,
                            "page_num": page_num,
                            "image_path": image_path,
                            "image_filename": image_filename,
                        }
                    )
                except Exception as e:
                    errors.append(
                        f"[{display_name}] page {page_num + 1} の変換に失敗しました: {e}"
                    )

    except Exception as e:
        errors.append(f"[{display_name}] PDFを開けませんでした: {e}")

    return {
        "pdf_index": pdf_index,
        "display_name": display_name,
        "upload_order": upload_order,
        "pages": page_manifest,
        "errors": errors,
    }


def pdf2images(
    pdf_manifest,
    output_dir: Path,
    bar,
    progress_start: int,
    progress_span: int,
    change_scale: str,
    progress_text: str,
):
    """
    PDF単位で並列変換する。
    """
    ensure_dir(output_dir)

    if not pdf_manifest:
        return [], [f"{progress_text}対象のPDFがありません。"], bar

    results = []
    errors = []

    tasks = [(pdf_info, output_dir, change_scale) for pdf_info in pdf_manifest]
    total = len(tasks)
    completed = 0

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_pdf = {
            executor.submit(convert_single_pdf_to_images, task): task[0]
            for task in tasks
        }

        for future in tqdm.tqdm(
            concurrent.futures.as_completed(future_to_pdf),
            total=total,
            desc=progress_text
        ):
            result = future.result()
            results.append(result)
            errors.extend(result["errors"])

            completed += 1
            current_progress = progress_start + int(progress_span * completed / total)
            bar.progress(min(current_progress, progress_start + progress_span), text=progress_text)

    results.sort(key=lambda x: x["upload_order"])

    return results, errors, bar


def flatten_pages(pdf_results):
    """
    PDF結果を、アップロード順 → ページ順で1本のページ列にフラット化する。
    """
    flat_pages = []

    ordered_results = sorted(pdf_results, key=lambda x: x["upload_order"])

    for pdf_result in ordered_results:
        ordered_pages = sorted(pdf_result["pages"], key=lambda x: x["page_num"])
        for page in ordered_pages:
            flat_pages.append(page)

    return flat_pages


def build_page_pairs(before_results, after_results):
    """
    PDF単位ではなく、全PDFを通したページ列で対応付ける。
    ファイル数が異なっていても、合計ページ数が同じなら比較可能。
    """
    errors = []
    pairs = []

    before_pages = flatten_pages(before_results)
    after_pages = flatten_pages(after_results)

    if len(before_pages) != len(after_pages):
        errors.append(
            f"合計ページ数が一致しません。元は {len(before_pages)} ページ、先は {len(after_pages)} ページです。"
        )
        return pairs, errors

    for global_page_index, (before_page, after_page) in enumerate(zip(before_pages, after_pages)):
        pairs.append(
            {
                "global_page_index": global_page_index,
                "before_pdf_index": before_page["pdf_index"],
                "before_display_name": before_page["display_name"],
                "before_page_num": before_page["page_num"],
                "before_image_path": before_page["image_path"],
                "after_pdf_index": after_page["pdf_index"],
                "after_display_name": after_page["display_name"],
                "after_page_num": after_page["page_num"],
                "after_image_path": after_page["image_path"],
            }
        )

    return pairs, errors


def compare_single_image_pair(args):
    """
    1ページ分の画像差分比較。
    スレッド内では UI を触らない。
    """
    pair_info, result_folder, color_bgr, bold = args

    before_path = str(pair_info["before_image_path"])
    after_path = str(pair_info["after_image_path"])
    global_page_index = pair_info["global_page_index"]

    result_filename = f"global_{global_page_index:04d}.jpg"
    result_path = result_folder / result_filename

    try:
        img_ref = cv2.imread(before_path)
        img_comp = cv2.imread(after_path)

        if img_ref is None:
            return {
                "ok": False,
                "error": (
                    f"[{pair_info['before_display_name']}] page {pair_info['before_page_num'] + 1}: "
                    "元画像を読み込めません。"
                ),
                "pair_info": pair_info,
            }

        if img_comp is None:
            return {
                "ok": False,
                "error": (
                    f"[{pair_info['after_display_name']}] page {pair_info['after_page_num'] + 1}: "
                    "比較画像を読み込めません。"
                ),
                "pair_info": pair_info,
            }

        if img_ref.shape != img_comp.shape:
            return {
                "ok": False,
                "error": (
                    f"画像サイズ不一致: "
                    f"元 '{pair_info['before_display_name']}' page {pair_info['before_page_num'] + 1} "
                    f"{img_ref.shape[1]}x{img_ref.shape[0]} / "
                    f"先 '{pair_info['after_display_name']}' page {pair_info['after_page_num'] + 1} "
                    f"{img_comp.shape[1]}x{img_comp.shape[0]}"
                ),
                "pair_info": pair_info,
            }

        output_img = img_comp.copy()

        gray_ref = cv2.cvtColor(img_ref, cv2.COLOR_BGR2GRAY)
        gray_comp = cv2.cvtColor(img_comp, cv2.COLOR_BGR2GRAY)

        diff = cv2.absdiff(gray_ref, gray_comp)
        _, diff_bin = cv2.threshold(diff, 50, 255, 0)
        diff_bin = cv2.bitwise_and(diff_bin, gray_ref)

        contours, _ = cv2.findContours(diff_bin, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        significant_contours = [c for c in contours if contour_is_significant(c)]
        has_diff = len(significant_contours) > 0

        for contour in significant_contours:
            x, y, w, h = cv2.boundingRect(contour)
            cv2.rectangle(
                output_img,
                (max(x - 2, 0), max(y - 2, 0)),
                (min(x + w + 2, output_img.shape[1] - 1), min(y + h + 2, output_img.shape[0] - 1)),
                color_bgr,
                bold,
            )

        write_ok = cv2.imwrite(str(result_path), output_img)
        if not write_ok:
            return {
                "ok": False,
                "error": (
                    f"[{pair_info['after_display_name']}] page {pair_info['after_page_num'] + 1}: "
                    "差分画像の保存に失敗しました。"
                ),
                "pair_info": pair_info,
            }

        return {
            "ok": True,
            "error": None,
            "pair_info": pair_info,
            "has_diff": has_diff,
            "result_path": result_path,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": (
                f"[{pair_info['after_display_name']}] page {pair_info['after_page_num'] + 1}: "
                f"差分検出中にエラーが発生しました: {e}"
            ),
            "pair_info": pair_info,
        }


def find_diff(page_pairs, result_folder: Path, color_bgr, bold, bar, progress_start: int = 70, progress_span: int = 30):
    """
    ページペア単位で並列差分検出する。
    """
    ensure_dir(result_folder)

    if not page_pairs:
        return [], ["比較対象ページがありません。"], bar

    results = []
    errors = []

    tasks = [(pair_info, result_folder, color_bgr, bold) for pair_info in page_pairs]
    total = len(tasks)
    completed = 0

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_pair = {
            executor.submit(compare_single_image_pair, task): task[0]
            for task in tasks
        }

        for future in tqdm.tqdm(
            concurrent.futures.as_completed(future_to_pair),
            total=total,
            desc="差分を検出中"
        ):
            result = future.result()
            if result["ok"]:
                results.append(result)
            else:
                errors.append(result["error"])

            completed += 1
            current_progress = progress_start + int(progress_span * completed / total)
            bar.progress(min(current_progress, 100), text="差分を検出中...")

    results.sort(key=lambda x: x["pair_info"]["global_page_index"])

    return results, errors, bar


def streamlit_main():
    st.title(":hammer_and_wrench: pdf difference checker :hammer_and_wrench:")
    st.divider()

    st.sidebar.title("Upload")
    before_pdf_files = st.sidebar.file_uploader(
        "突き合わせ元のpdf",
        accept_multiple_files=True,
        type="pdf",
        key="before_pdf_files",
    )

    st.sidebar.title("")
    after_pdf_files = st.sidebar.file_uploader(
        "突き合わせ先のpdf",
        accept_multiple_files=True,
        type="pdf",
        key="after_pdf_files",
    )

    st.sidebar.divider()
    st.sidebar.title("Options")

    change_scale = st.sidebar.selectbox(
        "差分チェックをするスケール",
        ("RGB", "GRAY")
    )

    color = st.sidebar.color_picker("マーキングする色", "#00ff00")

    bold = st.sidebar.slider(
        "差分を囲う線の太さ", 0, 10, 3
    )

    st.sidebar.divider()

    if not before_pdf_files or not after_pdf_files:
        st.warning(
            "突き合わせ元と突き合わせ先のPDFファイルは、比較したい順番でアップロードしてください。"
            "ファイル数が異なっていても、合計ページ数が同じであれば比較できます。",
            icon="⚠️"
        )
        st.warning(
            "対応ページのレンダリング後画像サイズが一致しない場合、そのページはエラーになります。",
            icon="⚠️"
        )
        st.warning("色の差分チェックは苦手です。ご了承ください。", icon="⚠️")
        return

    if st.button("突き合わせ開始"):
        before_temp_dir = None
        after_temp_dir = None

        try:
            success_box = st.empty()
            success_box.success("ファイルアップロード成功!")
            color_rgb = ImageColor.getcolor(color, "RGB")
            color_bgr = (color_rgb[2], color_rgb[1], color_rgb[0])
            time.sleep(0.8)
            success_box.empty()

            bar = st.progress(0, text="PDFファイルを読み込み中...")

            before_temp_dir = Path(tempfile.mkdtemp(prefix="before_pdf_"))
            after_temp_dir = Path(tempfile.mkdtemp(prefix="after_pdf_"))

            before_img_dir = ensure_dir(before_temp_dir / "before_pdf_img")
            after_img_dir = ensure_dir(after_temp_dir / "after_pdf_img")
            result_folder = ensure_dir(after_temp_dir / "result_folder")

            # 1) Save uploaded PDFs and build manifest
            before_manifest = build_pdf_manifest(before_pdf_files, before_temp_dir)
            after_manifest = build_pdf_manifest(after_pdf_files, after_temp_dir)

            bar.progress(10, text="元PDFをJPEGに変換中...")

            # 2) Convert before PDFs
            before_results, before_convert_errors, bar = pdf2images(
                pdf_manifest=before_manifest,
                output_dir=before_img_dir,
                bar=bar,
                progress_start=10,
                progress_span=30,
                change_scale=change_scale,
                progress_text="元PDFをJPEGに変換中..."
            )

            # 3) Convert after PDFs
            after_results, after_convert_errors, bar = pdf2images(
                pdf_manifest=after_manifest,
                output_dir=after_img_dir,
                bar=bar,
                progress_start=40,
                progress_span=30,
                change_scale=change_scale,
                progress_text="先PDFをJPEGに変換中..."
            )

            convert_errors = before_convert_errors + after_convert_errors
            if convert_errors:
                for err in convert_errors:
                    st.error(err)
                return

            # 4) Build page pairs by flattened page sequence
            page_pairs, pairing_errors = build_page_pairs(before_results, after_results)
            if pairing_errors:
                for err in pairing_errors:
                    st.error(err)
                return

            bar.progress(70, text="差分を検出中...")

            # 5) Compare page pairs
            diff_results, diff_errors, bar = find_diff(
                page_pairs=page_pairs,
                result_folder=result_folder,
                color_bgr=color_bgr,
                bold=bold,
                bar=bar,
                progress_start=70,
                progress_span=30,
            )

            if diff_errors:
                for err in diff_errors:
                    st.error(err)
                return

            bar.progress(100, text="完了!")
            time.sleep(0.5)
            bar.empty()

            if not diff_results:
                st.warning("比較結果がありませんでした。")
                return

            # 6) ZIP output
            zip_io = io.BytesIO()
            with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zip_file:
                st.toast("結果を表示中…", icon="🏃‍♂️")

                previous_after_name = None

                for result in diff_results:
                    pair_info = result["pair_info"]
                    before_name = pair_info["before_display_name"]
                    before_page_num = pair_info["before_page_num"]
                    after_name = pair_info["after_display_name"]
                    after_page_num = pair_info["after_page_num"]
                    before_img_path = pair_info["before_image_path"]
                    result_img_path = result["result_path"]

                    if previous_after_name is not None and previous_after_name != after_name:
                        st.divider()

                    if result["has_diff"]:
                        st.header(":bell: :red[There are differences]")

                    image_comparison(
                        img1=Image.open(before_img_path),
                        img2=Image.open(result_img_path),
                        label1=f"{before_name} (p.{before_page_num + 1})",
                        label2=f"{after_name} (p.{after_page_num + 1})",
                        width=700,
                        starting_position=1
                    )

                    with open(result_img_path, "rb") as img_file:
                        zip_file.writestr(
                            f"result_{after_name}_page_{after_page_num + 1:04d}_global_{pair_info['global_page_index'] + 1:04d}.jpg",
                            img_file.read()
                        )

                    previous_after_name = after_name

            st.divider()

            zip_io.seek(0)
            st.download_button(
                label="差分画像をZIPでダウンロード",
                data=zip_io,
                file_name="result.zip",
                mime="application/zip"
            )

            st.balloons()
            st.toast("全ての表示が完了しました！", icon="😍")

        except Exception as e:
            st.error(f"エラーが発生しました: {e}")

        finally:
            if before_temp_dir and before_temp_dir.exists():
                shutil.rmtree(before_temp_dir, ignore_errors=True)
            if after_temp_dir and after_temp_dir.exists():
                shutil.rmtree(after_temp_dir, ignore_errors=True)


def main():
    st.set_page_config(
        page_title="Pdf Difference Checker",
        page_icon=":file_cabinet:",
        initial_sidebar_state="expanded"
    )
    streamlit_main()


if __name__ == "__main__":
    main()
