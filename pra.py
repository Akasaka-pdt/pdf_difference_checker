import streamlit as st


after_pdf_file = st.sidebar.file_uploader("突き合わせ先のpdf", accept_multiple_files=True, type = "pdf")