"""Run offline quality UI: python -m streamlit run check_skills.py"""

import streamlit as st

from ui.skill_evaluation_view import render_skill_evaluation

st.set_page_config(page_title="Проверка навыков", layout="wide")
render_skill_evaluation()
