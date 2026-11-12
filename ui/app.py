"""Import archives and rank the persistent MongoDB corpus through Neo4j."""
import streamlit as st

from ui.results_view import render_results
from ui.upload_view import render_upload


def main():
    st.set_page_config(page_title="Подбор кандидатов", page_icon="📄", layout="wide")
    st.title("Подбор кандидатов")
    render_upload()
    render_results()


if __name__ == "__main__":
    main()
