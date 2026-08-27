{pkgs, lib, version}:
let
  python = pkgs.python312.withPackages (ps:
    with ps; [
      aiohttp
      beautifulsoup4
      httpx
      jinja2
      lxml
      matplotlib
      numpy
      odfpy
      openpyxl
      pandas
      pdfplumber
      pillow
      pymupdf
      pypdf
      pytest
      python-docx
      python-pptx
      pyyaml
      reportlab
      requests
      scikit-learn
      scipy
      seaborn
      xlrd
    ]);

  tools = with pkgs; [
    bashInteractive
    coreutils
    gnused
    gawk
    gnugrep
    findutils
    diffutils
    patch
    file
    tree
    bc
    less
    which
    procps
    psmisc
    lsof
    util-linux
    hostname
    gnutar
    gzip
    xz
    bzip2
    zstd
    zip
    unzip
    p7zip
    curl
    wget
    openssl
    rsync
    socat
    netcat-gnu
    iproute2
    iputils
    dnsutils
    openssh
    cacert
    git
    vim
    nano
    jq
    ripgrep
    gnumake
    gcc
    clang
    cmake
    ninja
    pkg-config
    gdb
    shellcheck
    python
    nodejs_22
    go
    rustc
    cargo
    jdk21_headless
    sqlite
    postgresql
    ffmpeg-headless
    poppler-utils
    qpdf
    pandoc
    libreoffice-fresh
    imagemagick
    ghostscript
    tesseract5
    graphviz
    exiftool
    mediainfo
    yt-dlp
    fontconfig
    noto-fonts-cjk-sans
  ];
in
pkgs.dockerTools.buildLayeredImage {
  name = "kennethbot-sandbox";
  tag = "latest";
  maxLayers = 120;
  contents = tools ++ [
    pkgs.dockerTools.binSh
    pkgs.dockerTools.usrBinEnv
  ];

  extraCommands = ''
    mkdir -p workspace home/sandbox tmp etc
    chmod 0755 workspace home/sandbox
    chmod 1777 tmp
    chown 1000:1000 workspace home/sandbox
    printf 'sandbox:x:1000:1000:Kennethbot sandbox:/home/sandbox:/bin/sh\n' > etc/passwd
    printf 'sandbox:x:1000:\n' > etc/group
    printf 'hosts: files dns\n' > etc/nsswitch.conf
    printf '%s\n' \
      'Languages: Python 3.12, Node.js 22, Go, Rust, Java 21' \
      'Development: git, gcc, clang, cmake, ninja, make, gdb, shellcheck' \
      'Documents: poppler, qpdf, pandoc, LibreOffice, Python Office libraries' \
      'Media: ffmpeg, ImageMagick, Tesseract, Graphviz, ExifTool, yt-dlp' \
      'Data: pandas, NumPy, SciPy, scikit-learn, SQLite, PostgreSQL client' \
      > etc/kennethbot-sandbox-tools
  '';

  config = {
    User = "1000:1000";
    WorkingDir = "/workspace";
    Cmd = ["${pkgs.coreutils}/bin/sleep" "infinity"];
    Env = [
      "PATH=${lib.makeBinPath tools}:/bin:/usr/bin"
      "HOME=/home/sandbox"
      "USER=sandbox"
      "LANG=C.UTF-8"
      "LC_ALL=C.UTF-8"
      "PYTHONUNBUFFERED=1"
      "MPLCONFIGDIR=/tmp/matplotlib"
      "XDG_CACHE_HOME=/tmp/cache"
      "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
    ];
    Labels = {
      "org.opencontainers.image.title" = "Kennethbot advanced sandbox";
      "org.opencontainers.image.version" = version;
      "io.kennethbot.sandbox" = "advanced";
    };
  };
}
