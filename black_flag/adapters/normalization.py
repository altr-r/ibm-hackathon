"""
adapters/normalization.py — Curated generic→distro package name table.

Keys are generic names used internally (and in primitive params).
Values map to the correct package name for each distro's package manager.

Covers ~30 commonly used packages sufficient for the hackathon demo and
most real-world Python/shell portability issues.
"""

# Structure: generic_name -> {distro_id: package_name}
# distro_id matches the value of $ID in /etc/os-release
NORMALIZATION_TABLE: dict[str, dict[str, str]] = {
    # SSL / TLS
    "ssl-dev": {
        "ubuntu": "libssl-dev",
        "debian": "libssl-dev",
        "fedora": "openssl-devel",
        "rhel":   "openssl-devel",
        "centos": "openssl-devel",
        "arch":   "openssl",
    },
    # Python cryptography (pip name is "cryptography"; distro packages vary)
    "python-cryptography": {
        "ubuntu": "python3-cryptography",
        "debian": "python3-cryptography",
        "fedora": "python3-cryptography",
        "rhel":   "python3-cryptography",
        "arch":   "python-cryptography",
    },
    # Python 3
    "python3": {
        "ubuntu": "python3",
        "debian": "python3",
        "fedora": "python3",
        "rhel":   "python3",
        "arch":   "python",
    },
    # Python 3 pip
    "python3-pip": {
        "ubuntu": "python3-pip",
        "debian": "python3-pip",
        "fedora": "python3-pip",
        "rhel":   "python3-pip",
        "arch":   "python-pip",
    },
    # cURL
    "curl": {
        "ubuntu": "curl",
        "debian": "curl",
        "fedora": "curl",
        "rhel":   "curl",
        "arch":   "curl",
    },
    # wget
    "wget": {
        "ubuntu": "wget",
        "debian": "wget",
        "fedora": "wget",
        "rhel":   "wget",
        "arch":   "wget",
    },
    # git
    "git": {
        "ubuntu": "git",
        "debian": "git",
        "fedora": "git",
        "rhel":   "git",
        "arch":   "git",
    },
    # make
    "make": {
        "ubuntu": "make",
        "debian": "make",
        "fedora": "make",
        "rhel":   "make",
        "arch":   "make",
    },
    # gcc
    "gcc": {
        "ubuntu": "gcc",
        "debian": "gcc",
        "fedora": "gcc",
        "rhel":   "gcc",
        "arch":   "gcc",
    },
    # build essentials / development tools
    "build-essential": {
        "ubuntu": "build-essential",
        "debian": "build-essential",
        "fedora": "gcc make",
        "rhel":   "gcc make",
        "arch":   "base-devel",
    },
    # zlib development headers
    "zlib-dev": {
        "ubuntu": "zlib1g-dev",
        "debian": "zlib1g-dev",
        "fedora": "zlib-devel",
        "rhel":   "zlib-devel",
        "arch":   "zlib",
    },
    # libffi
    "libffi-dev": {
        "ubuntu": "libffi-dev",
        "debian": "libffi-dev",
        "fedora": "libffi-devel",
        "rhel":   "libffi-devel",
        "arch":   "libffi",
    },
    # readline
    "readline-dev": {
        "ubuntu": "libreadline-dev",
        "debian": "libreadline-dev",
        "fedora": "readline-devel",
        "rhel":   "readline-devel",
        "arch":   "readline",
    },
    # sqlite3
    "sqlite3-dev": {
        "ubuntu": "libsqlite3-dev",
        "debian": "libsqlite3-dev",
        "fedora": "sqlite-devel",
        "rhel":   "sqlite-devel",
        "arch":   "sqlite",
    },
    # bzip2
    "bzip2-dev": {
        "ubuntu": "libbz2-dev",
        "debian": "libbz2-dev",
        "fedora": "bzip2-devel",
        "rhel":   "bzip2-devel",
        "arch":   "bzip2",
    },
    # lzma / xz
    "lzma-dev": {
        "ubuntu": "liblzma-dev",
        "debian": "liblzma-dev",
        "fedora": "xz-devel",
        "rhel":   "xz-devel",
        "arch":   "xz",
    },
    # ncurses
    "ncurses-dev": {
        "ubuntu": "libncurses-dev",
        "debian": "libncurses-dev",
        "fedora": "ncurses-devel",
        "rhel":   "ncurses-devel",
        "arch":   "ncurses",
    },
    # Apache web server
    "apache": {
        "ubuntu": "apache2",
        "debian": "apache2",
        "fedora": "httpd",
        "rhel":   "httpd",
        "arch":   "apache",
    },
    # nginx
    "nginx": {
        "ubuntu": "nginx",
        "debian": "nginx",
        "fedora": "nginx",
        "rhel":   "nginx",
        "arch":   "nginx",
    },
    # MySQL / MariaDB client
    "mysql-client": {
        "ubuntu": "default-mysql-client",
        "debian": "default-mysql-client",
        "fedora": "mariadb",
        "rhel":   "mariadb",
        "arch":   "mariadb-clients",
    },
    # MySQL / MariaDB dev headers
    "mysql-dev": {
        "ubuntu": "libmysqlclient-dev",
        "debian": "libmysqlclient-dev",
        "fedora": "mariadb-devel",
        "rhel":   "mariadb-devel",
        "arch":   "libmariadbclient",
    },
    # PostgreSQL client
    "postgresql-client": {
        "ubuntu": "postgresql-client",
        "debian": "postgresql-client",
        "fedora": "postgresql",
        "rhel":   "postgresql",
        "arch":   "postgresql-libs",
    },
    # Redis
    "redis": {
        "ubuntu": "redis-server",
        "debian": "redis-server",
        "fedora": "redis",
        "rhel":   "redis",
        "arch":   "redis",
    },
    # jq (JSON processor)
    "jq": {
        "ubuntu": "jq",
        "debian": "jq",
        "fedora": "jq",
        "rhel":   "jq",
        "arch":   "jq",
    },
    # unzip
    "unzip": {
        "ubuntu": "unzip",
        "debian": "unzip",
        "fedora": "unzip",
        "rhel":   "unzip",
        "arch":   "unzip",
    },
    # tar (always present, included for completeness)
    "tar": {
        "ubuntu": "tar",
        "debian": "tar",
        "fedora": "tar",
        "rhel":   "tar",
        "arch":   "tar",
    },
    # ca-certificates
    "ca-certificates": {
        "ubuntu": "ca-certificates",
        "debian": "ca-certificates",
        "fedora": "ca-certificates",
        "rhel":   "ca-certificates",
        "arch":   "ca-certificates",
    },
    # OpenSSH client
    "openssh-client": {
        "ubuntu": "openssh-client",
        "debian": "openssh-client",
        "fedora": "openssh-clients",
        "rhel":   "openssh-clients",
        "arch":   "openssh",
    },
    # rsync
    "rsync": {
        "ubuntu": "rsync",
        "debian": "rsync",
        "fedora": "rsync",
        "rhel":   "rsync",
        "arch":   "rsync",
    },
    # strace (debugging)
    "strace": {
        "ubuntu": "strace",
        "debian": "strace",
        "fedora": "strace",
        "rhel":   "strace",
        "arch":   "strace",
    },
}

# Service name mapping: Debian service name → distro-specific name
SERVICE_NAME_TABLE: dict[str, dict[str, str]] = {
    "apache2": {
        "ubuntu": "apache2",
        "debian": "apache2",
        "fedora": "httpd",
        "rhel":   "httpd",
        "arch":   "httpd",
    },
    "networking": {
        "ubuntu": "networking",
        "debian": "networking",
        "fedora": "NetworkManager",
        "rhel":   "NetworkManager",
        "arch":   "NetworkManager",
    },
    "mysql": {
        "ubuntu": "mysql",
        "debian": "mysql",
        "fedora": "mariadb",
        "rhel":   "mariadb",
        "arch":   "mariadb",
    },
    "ssh": {
        "ubuntu": "ssh",
        "debian": "ssh",
        "fedora": "sshd",
        "rhel":   "sshd",
        "arch":   "sshd",
    },
    "cron": {
        "ubuntu": "cron",
        "debian": "cron",
        "fedora": "crond",
        "rhel":   "crond",
        "arch":   "cronie",
    },
}


def resolve_package(generic_name: str, distro_id: str) -> str | None:
    """
    Return the distro-specific package name for *generic_name*, or None if
    the generic name is not in the table.

    *distro_id* is the value of $ID from /etc/os-release (e.g. "ubuntu", "fedora", "arch").
    """
    entry = NORMALIZATION_TABLE.get(generic_name)
    if entry is None:
        return None
    # Exact match first, then fall back to the "ubuntu" key as a last resort
    return entry.get(distro_id) or entry.get("ubuntu")


def resolve_service(debian_name: str, distro_id: str) -> str | None:
    """Return the distro-specific service name, or None if not mapped."""
    entry = SERVICE_NAME_TABLE.get(debian_name)
    if entry is None:
        return None
    return entry.get(distro_id) or entry.get("ubuntu")
