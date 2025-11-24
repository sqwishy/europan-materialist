FROM alpine:latest

RUN apk update \
 && apk add python3 py3-lxml py3-pillow
ADD baro-data.py .
ENTRYPOINT ["python3", "baro-data.py"]
