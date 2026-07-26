#!/usr/bin/env bash
# Первичная настройка VPS под сервис скачивания и анализа файлов.
# Ubuntu 24.04 LTS, 1 vCPU / 2 GB RAM / 25 GB.
#
# Запускается от root. Стадии разделены, чтобы каждая укладывалась в таймаут:
#   STAGE=1 — система, swap, пользователь app
#   STAGE=2 — Docker, uv
#   STAGE=3 — ufw, fail2ban, автообновления
#   STAGE=4 — хардening sshd (только после проверки входа по ключу!)
#
# Скрипт идемпотентен: повторный запуск безопасен.

set -euo pipefail

APP_USER=app
SWAP_SIZE=2G

log() { echo "==> $*"; }

# Свежая машина часто занята cloud-init/unattended-upgrades — ждём apt.
wait_for_apt() {
    local i=0
    while fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; do
        i=$((i + 1))
        [ "$i" -gt 60 ] && { echo "apt заблокирован слишком долго"; exit 1; }
        sleep 5
    done
}

stage1() {
    log "таймзона UTC (НСК рендерится на уровне приложения)"
    timedatectl set-timezone UTC

    log "обновление системы"
    wait_for_apt
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get -qq -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
    apt-get -qq -y install ca-certificates curl gnupg git make jq unzip

    log "swap ${SWAP_SIZE} (2 GB RAM маловато для сборки образов)"
    if [ ! -f /swapfile ]; then
        fallocate -l "$SWAP_SIZE" /swapfile
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
        swapon /swapfile
        grep -qxF '/swapfile none swap sw 0 0' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
        sysctl -qw vm.swappiness=10
        grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >>/etc/sysctl.conf
    fi

    log "пользователь ${APP_USER}"
    if ! id -u "$APP_USER" >/dev/null 2>&1; then
        adduser --disabled-password --gecos "" "$APP_USER"
    fi
    usermod -aG sudo "$APP_USER"
    echo "$APP_USER ALL=(ALL) NOPASSWD:ALL" >"/etc/sudoers.d/90-$APP_USER"
    chmod 440 "/etc/sudoers.d/90-$APP_USER"

    log "перенос ssh-ключей root -> ${APP_USER}"
    install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
    cp /root/.ssh/authorized_keys "/home/$APP_USER/.ssh/authorized_keys"
    chown "$APP_USER:$APP_USER" "/home/$APP_USER/.ssh/authorized_keys"
    chmod 600 "/home/$APP_USER/.ssh/authorized_keys"

    free -h | head -3
}

stage2() {
    log "Docker CE из официального репозитория"
    if ! command -v docker >/dev/null 2>&1; then
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
            gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
            >/etc/apt/sources.list.d/docker.list
        wait_for_apt
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get -qq -y install \
            docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    usermod -aG docker "$APP_USER"
    systemctl enable --now docker

    log "ограничение логов docker, чтобы не съесть диск"
    cat >/etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
    systemctl restart docker

    log "uv для пользователя ${APP_USER}"
    sudo -u "$APP_USER" -H bash -lc 'command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh'

    docker --version
    docker compose version
    sudo -u "$APP_USER" -H bash -lc '$HOME/.local/bin/uv --version'
}

stage3() {
    log "firewall"
    wait_for_apt
    DEBIAN_FRONTEND=noninteractive apt-get -qq -y install ufw fail2ban unattended-upgrades
    ufw allow OpenSSH
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable

    log "fail2ban на sshd"
    cat >/etc/fail2ban/jail.d/sshd.local <<'INI'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
INI
    systemctl enable --now fail2ban

    log "автоматические security-обновления"
    cat >/etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

    ufw status verbose
}

stage4() {
    log "проверка, что вход по ключу возможен, до отключения паролей"
    for u in root "$APP_USER"; do
        home=$(getent passwd "$u" | cut -d: -f6)
        if [ ! -s "$home/.ssh/authorized_keys" ]; then
            echo "ОТМЕНА: у $u пустой authorized_keys — отключение пароля заблокирует доступ"
            exit 1
        fi
    done

    log "отключение парольного входа и root-логина"
    rm -f /etc/ssh/sshd_config.d/50-cloud-init.conf
    cat >/etc/ssh/sshd_config.d/99-hardening.conf <<'CONF'
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
CONF
    sshd -t
    systemctl restart ssh
    sshd -T | grep -Ei "^(passwordauthentication|permitrootlogin|pubkeyauthentication)"
}

case "${STAGE:?нужно задать STAGE=1|2|3|4}" in
    1) stage1 ;;
    2) stage2 ;;
    3) stage3 ;;
    4) stage4 ;;
    *) echo "неизвестная стадия: $STAGE"; exit 1 ;;
esac

log "стадия $STAGE завершена"
