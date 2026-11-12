"""Import archives and rank the persistent MongoDB corpus through Neo4j."""
import streamlit as st

from ui.results_view import render_results
from ui.upload_view import render_upload


def localize_controls():
    """Russian visible labels for the built-in Streamlit 1.41–1.49 uploader."""
    st.set_option("client.toolbarMode", "minimal")
    upload_limit = st.get_option("server.maxUploadSize")
    # Streamlit has no public API for the uploader's built-in text.
    # Scope replacements to upload controls; candidate/document text stays intact.
    st.html(
        """
        <style>
        [data-testid="stAppDeployButton"],
        [data-testid="stMainMenu"] {
            display: none;
        }
        [data-testid="stFileUploaderDropzone"] button {
            font-size: 0;
        }
        [data-testid="stFileUploaderDropzone"] button::after {
            content: "Выбрать файл";
            font-size: 0.875rem;
        }
        [data-testid="stFileUploaderDropzoneInstructions"] > div > span {
            font-size: 0;
        }
        [data-testid="stFileUploaderDropzoneInstructions"] > div > span:first-child::after {
            content: "Перетащите ZIP-архив сюда";
            font-size: 1rem;
        }
        [data-testid="stFileUploaderDropzoneInstructions"] > div > span:last-child::after {
            content: "ZIP · до UPLOAD_LIMIT МБ на файл";
            font-size: 0.875rem;
        }
        </style>
        """.replace("UPLOAD_LIMIT", str(upload_limit))
    )


def main():
    st.set_page_config(page_title="Подбор кандидатов", page_icon="📄", layout="wide")
    localize_controls()
    st.title("Подбор кандидатов")
    render_upload()
    render_results()


if __name__ == "__main__":
    main()
