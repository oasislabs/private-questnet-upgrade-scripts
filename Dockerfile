FROM ubuntu:bionic

RUN apt update && \
    apt install python-pip python-dev
