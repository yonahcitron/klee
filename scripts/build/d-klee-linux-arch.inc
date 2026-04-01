# Create ``klee`` user for container with password ``klee``.
# and give it password-less sudo access (temporarily so we can use the CI scripts)
RUN pacman -Syu --noconfirm && pacman -S --noconfirm sudo emacs-nox vim file python-dateutil python-tabulate && \
    pacman -Scc --noconfirm && \
    useradd -m klee && \
    echo klee:klee | chpasswd && \
    cp /etc/sudoers /etc/sudoers.bak && \
    echo 'klee  ALL=(root) NOPASSWD: ALL' >> /etc/sudoers

# Copy across source files needed for build
COPY --chown=klee:klee . /tmp/klee_src/

USER klee
WORKDIR /home/klee
# Build and set klee user to be owner
RUN /tmp/klee_src/scripts/build/build.sh --debug --install-system-deps klee && \
    /usr/bin/python3 -m venv --system-site-packages /tmp/pip-venv && \
    /tmp/pip-venv/bin/python -m pip install --quiet flask wllvm && \
    sudo pacman -Scc --noconfirm

ENV PATH="$PATH:/home/klee/klee_build/bin:/home/klee/.local/bin"
ENV BASE=/tmp

# Dynamically add source-built LLVM tools to PATH (version varies per build)
RUN /bin/bash -c 'llvm_bin=$(find ${BASE} -maxdepth 2 -name llvm-config -path "*install*" -printf "%h" -quit 2>/dev/null); \
    [[ -n "${llvm_bin}" ]] && echo "export PATH=\"\$PATH:${llvm_bin}\"" >> ~/.bashrc || true'

# Add KLEE header files to system standard include folder
RUN sudo /bin/bash -c 'ln -s /tmp/klee_src/include/klee /usr/include/'

ENV LD_LIBRARY_PATH=/home/klee/klee_build/lib/

# Add KLEE binary directory to PATH
RUN /bin/bash -c 'ln -s ${BASE}/klee_src /home/klee/ && ln -s ${BASE}/klee_build* /home/klee/klee_build'
