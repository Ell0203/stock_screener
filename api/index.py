import sys
import os

# 현재 api 폴더의 상위 폴더(루트 경로)를 파이썬 경로에 추가합니다.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 루트 경로에 있는 app.py 안의 Flask 인스턴스(app)를 가져옵니다.
from app import app
