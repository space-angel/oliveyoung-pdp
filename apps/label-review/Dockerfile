# 라벨 검수 앱 (PER-178) — Render 등 컨테이너 호스트용. 표준 라이브러리만 쓰므로 pip install 이 없다.
# 빌드 컨텍스트는 **저장소 루트**다 (service.py 가 pipeline/ · eval/ · data/input/ 을 상대 경로로 읽는다):
#   docker build -f apps/label-review/Dockerfile -t label-review .
#   docker run -p 8180:8180 -e SUPABASE_URL=… -e SUPABASE_SERVICE_KEY=… -e LABEL_ACCESS_CODE=… label-review
# 제외 목록은 저장소 루트의 .dockerignore (data/intermediate · data/cache · .env · legacy 등).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8180

WORKDIR /app
COPY . /app

# 서버가 읽는 파일이 이미지에 다 들어왔는지 빌드 시점에 확인한다 — 빠지면 여기서 실패한다
RUN test -f apps/label-review/server.py \
 && test -f apps/label-review/public/index.html \
 && test -f pipeline/golden_contract.py \
 && test -f pipeline/failure_taxonomy.json \
 && test -f eval/aspect_keywords.json \
 && test -f eval/gold/v5_concern_golden_sample.jsonl \
 && test -f eval/gold/v5_concern_golden_candidates.jsonl \
 && test -f data/input/skin_codebook.json

EXPOSE 8180
# Render 는 $PORT 를 넣어 준다. 없으면 8180
CMD ["sh", "-c", "python3 apps/label-review/server.py --port ${PORT:-8180}"]
