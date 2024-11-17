FROM osrf/ros:humble-desktop-full-jammy


RUN apt update && apt-get install -y --no-install-recommends \
    make build-essential libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev wget curl llvm libncurses5-dev \
    xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    git mecab-ipadic-utf8


RUN touch /usr/local/etc/mecabrc

ENV HOME="/root"
WORKDIR ${HOME}
RUN git clone --depth=1 https://github.com/pyenv/pyenv.git .pyenv
ENV PYENV_ROOT="${HOME}/.pyenv"
ENV PATH="${PYENV_ROOT}/shims:${PYENV_ROOT}/bin:${PATH}"

ENV PYTHON_VERSION=3.12
RUN pyenv install ${PYTHON_VERSION} && pyenv global ${PYTHON_VERSION}

RUN pip install poetry==1.4.2
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

COPY poetry.lock pyproject.toml ./
RUN poetry install --no-dev --no-root

COPY . .

CMD ["poetry", "run", "uvicorn", "ros_node_manager.main:app", "--host=0.0.0.0", "--port=8000"]
