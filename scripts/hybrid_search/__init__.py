"""C 파트(검색/RAG) 수동 실행 도구.
 
pytest 대상이 아니다 — 실제 OpenSearch·PostgreSQL·Cloudflare에 접속해
측정·튜닝·평가를 수행하는 스크립트 모음이다.
 
프로젝트 루트에서 실행한다.
    python scripts/hybrid_search/eval_search.py
 
각 파일이 루트를 sys.path에 넣으므로 하위 폴더에서도 agents를 찾는다.
"""
 