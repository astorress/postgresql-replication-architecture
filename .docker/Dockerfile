######## BASE IMAGE ########
FROM python:3.13-slim-trixie AS img-base

# Arguments
ARG USER=postgresql-replication
ARG USER_HOME=/home/$USER
ARG VOLUME=/app
ARG SHELL=/bin/bash
ARG PG_MAJOR=18

RUN pip3 install --upgrade pip

RUN apt-get update

# Inital OS configuration
RUN apt-get install -y locales \
    && sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen
RUN apt-get install -y sudo git curl jq less procps

# PostgreSQL client tools (psql, pg_dump, pg_basebackup...) from the PGDG repository, same major as the nodes
RUN apt-get install -y postgresql-common \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install -y postgresql-client-$PG_MAJOR
RUN psql --version

# Set up non root user
RUN echo "$USER ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
RUN useradd -ms $SHELL $USER
RUN mkdir -p $VOLUME \
    && chown -R $USER:$USER $VOLUME

# Preserve bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
    && echo $SNIPPET >> "/root/.bashrc" \
    && mkdir /commandhistory \
    && touch /commandhistory/.bash_history \
    && chown -R $USER /commandhistory \
    && echo $SNIPPET >> "$USER_HOME/.bashrc"


######## LAB IMAGE ########
FROM img-base AS img-lab

# Install VSCode extensions dependencies
RUN pip3 install pylint yapf isort

USER $USER
