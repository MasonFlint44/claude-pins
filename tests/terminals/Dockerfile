# Linux terminals under Xvfb, for pressing every key the picker binds and reading back what each
# terminal delivered (pin _keys), plus one screenshot of the picker per terminal.
FROM archlinux:latest
RUN pacman -Syu --noconfirm && pacman -S --noconfirm --needed \
      xorg-server-xvfb xorg-xauth xdotool openbox xterm gnome-terminal konsole kitty alacritty ghostty \
      tmux mesa libglvnd imagemagick python git dbus ttf-dejavu noto-fonts noto-fonts-emoji \
    && pacman -Scc --noconfirm
RUN sed -i 's/^#en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
RUN useradd -m tester
WORKDIR /work
