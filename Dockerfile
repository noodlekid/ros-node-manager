# Use the ROS2 Humble desktop full image as the base
FROM osrf/ros:humble-desktop-full-jammy

# Set environment variables for non-interactive installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies and clean up apt cache to reduce image size
RUN apt-get update && apt-get install -y --no-install-recommends \
    make build-essential libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev wget curl llvm libncurses5-dev \
    xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    git mecab-ipadic-utf8 \
    && rm -rf /var/lib/apt/lists/*


# Create and set the home directory
ENV HOME="/root"
WORKDIR ${HOME}


# Install pyenv
RUN git clone --depth=1 https://github.com/pyenv/pyenv.git ${HOME}/.pyenv

ENV PYENV_ROOT="${HOME}/.pyenv"
ENV PATH="${PYENV_ROOT}/bin:${PYENV_ROOT}/shims:${PATH}"

# Install Python 3.12.0 using pyenv and set it as the global Python version
RUN pyenv install 3.12.0 && pyenv global 3.12.0

# Install Poetry using the official installation script
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s ${HOME}/.local/bin/poetry /usr/local/bin/poetry

# Set environment variables for Poetry

ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache \
    PATH="${HOME}/.local/bin:${PATH}"

# Set the working directory for the application
WORKDIR /app

# Copy only the dependency files to leverage Docker cache
RUN poetry install --no-dev --no-root


# Install Python dependencies without installing the current project
RUN poetry install --no-dev

# Copy the rest of the application code
COPY . .

EXPOSE 8000

CMD ["poetry", "run", "uvicorn", "ros_node_manager.main:app", "--host=0.0.0.0", "--port=8000"]
