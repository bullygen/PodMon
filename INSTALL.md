# Установка Podmon на Ubuntu-сервере

Эта инструкция устанавливает мониторинг **всех rootless Podman-контейнеров одного Linux-пользователя**. Никакие имена контейнеров и каталоги вычислений вводить в Podmon не надо. Код клонируется на сервер из GitHub по SSH deploy key, образ собирается **на сервере**, затем rootless Quadlet запускает его через user systemd. Схема `linger=yes` сохраняет user manager после SSH logout и запускает его после reboot.

Во всех командах замените `user`, `SERVER_LAN_IP`, `LAN_IP` и `LAN_SUBNET` своими значениями. Работайте от того же пользователя, который создал вычислительные контейнеры. `sudo podman` увидит другое хранилище.

## 1. Проверить сервер

Подключитесь по SSH и проверьте Podman:

```bash
ssh user@SERVER_LAN_IP
whoami
podman --version
podman info --format 'rootless={{.Host.Security.Rootless}} cgroup={{.Host.CgroupVersion}}'
podman ps -a
```

Для клонирования и серверной сборки нужны Git, SSH client и сетевой доступ к GitHub, базовому образу и пакетам Python во время `podman build`. Если Git/SSH client отсутствуют:

```bash
sudo apt-get update
sudo apt-get install -y git openssh-client
```

Python, pip, GCC, Node.js и nginx на хосте не нужны.

## 2. Создать ED25519 deploy key **на сервере**

На сервере:

```bash
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
ssh-keygen -t ed25519 -C "podmon@$(hostname)" -f "$HOME/.ssh/podmon_deploy_ed25519"
chmod 600 "$HOME/.ssh/podmon_deploy_ed25519"
chmod 644 "$HOME/.ssh/podmon_deploy_ed25519.pub"
cat "$HOME/.ssh/podmon_deploy_ed25519.pub"
```

Скопируйте **только содержимое `.pub`** в нужном GitHub-репозитории: `Settings → Deploy keys → Add deploy key`. Оставьте `Allow write access` выключенным. Приватный файл `podmon_deploy_ed25519` остаётся на сервере вне репозитория и никогда не передаётся в контейнер. Deploy key даёт доступ только к одному репозиторию; это штатный [механизм GitHub](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).

Добавьте SSH alias на сервере:

```bash
cat >> "$HOME/.ssh/config" <<'SSHCONFIG'
Host github-podmon
    HostName github.com
    User git
    IdentityFile ~/.ssh/podmon_deploy_ed25519
    IdentitiesOnly yes
SSHCONFIG
chmod 600 "$HOME/.ssh/config"
ssh -T github-podmon
```

При первом соединении SSH покажет fingerprint `github.com`. Сравните его с [официальными fingerprint GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints) перед подтверждением. Сообщение GitHub «successfully authenticated» при `ssh -T` является ожидаемым; GitHub не предоставляет shell.

Клонируйте код на сервере:

```bash
git clone git@github-podmon:bullygen/PodMon.git "$HOME/podmon-src"
cd "$HOME/podmon-src"
```

В репозитории находятся `README.md`, `INSTALL.md` и каталог `service/`. GitHub-репозиторий `bullygen/PodMon` сейчас публичный; deploy key всё равно позволяет использовать запрошенный SSH-процесс клонирования.

## 3. LAN firewall и запуск

Укажите адрес **LAN-интерфейса сервера**. Пример ограничивающего правила UFW; подставьте настоящую подсеть и IP:

```bash
LAN_IP=192.168.1.20
LAN_SUBNET=192.168.1.0/24
sudo ufw allow from "$LAN_SUBNET" to "$LAN_IP" port 8080 proto tcp
sudo ufw status
```

Если UFW не используется, добавьте эквивалентное правило в существующий firewall. Не открывайте порт 8080 в Интернет. Сервис публикует HTTP только на `LAN_IP:8080`.

В корне клонированного репозитория запустите:

```bash
bash service/scripts/install_server.sh "$LAN_IP"
```

Скрипт:

1. проверяет или включает `linger=yes` через `sudo loginctl enable-linger`;
2. включает `systemctl --user enable --now podman.socket`;
3. выполняет `podman build -t localhost/podmon:latest service` **на сервере**;
4. пробует read-only mounts отдельных host-файлов для метрик (при неудаче оставляет их отключёнными);
5. создаёт `~/.config/containers/systemd/podmon.container` и запускает `podmon.service`;
6. проверяет, что Podmon читает Podman API через Unix socket.

`[Install] WantedBy=default.target` включает generated Quadlet service в user default target при старте manager. Отдельное `systemctl --user enable podmon.service` для generated Quadlet обычно не поддерживается; после `daemon-reload` установщик делает `restart`, а после reboot user manager запустит сервис благодаря `linger=yes`. `Restart=always` в systemd отвечает за перезапуск после сбоя. Это соответствует [документации Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).

## 4. Проверить панель и автозапуск

На сервере:

```bash
loginctl show-user "$USER" -p Linger
systemctl --user status podman.socket podmon.service
podman inspect podmon --format '{{.State.Status}}'
bash service/scripts/smoke_test.sh "http://$LAN_IP:8080"
bash service/scripts/survival_test.sh "$LAN_IP"
```

Ожидается `Linger=yes`, `podmon.service active`, контейнер `running` и успешные проверки. Откройте `http://LAN_IP:8080/` с другого компьютера в LAN. Сравните количество и имена карточек с `podman ps -a`; найдите контейнер и команду процесса через поле поиска. Новые контейнеры появляются автоматически при следующем опросе.

**Обязательный тест SSH logout:** полностью выйдите из SSH (`exit`), подождите несколько минут, снова откройте панель из браузера, затем войдите по SSH и повторите команды выше. После согласованного reboot сервера повторите проверку ещё раз. Вычислительные контейнеры имеют собственную политику запуска; Podmon их не перезапускает и не меняет.

## 5. Обновление с GitHub

На сервере под тем же пользователем:

```bash
cd "$HOME/podmon-src"
git pull --ff-only
bash service/scripts/install_server.sh "$LAN_IP"
```

Скрипт пересобирает image и перезапускает Quadlet service. Deploy key нужен только хосту для `git pull`; podmon не получает SSH-доступ.

## 6. Диагностика

```bash
systemctl --user status podmon.service podman.socket
journalctl --user -u podmon.service -n 100 --no-pager
podman logs --tail 100 podmon
podman ps -a
```

- `Permission denied (publickey)` при clone: проверьте public deploy key в **этом** репозитории, SSH alias, права `~/.ssh` и `ssh -T github-podmon`.
- `Linger=no` или podmon умирает после logout: выполните `sudo loginctl enable-linger "$USER"` и повторите полный тест выхода. Один `podman run -d` не заменяет linger.
- `podmon.service not found`: проверьте файл `~/.config/containers/systemd/podmon.container`, затем `systemctl --user daemon-reload`; для ошибок генератора: `/usr/lib/systemd/system-generators/podman-system-generator --user --dryrun`.
- Podman socket недоступен: проверьте `podman.socket`, UID/GID в Quadlet и `/run/user/$(id -u)/podman/podman.sock`. Если именно `:ro` socket bind не поддерживается данной версией, снимите `:ro` только с этой строки `Volume=` и повторите проверку; сам API от этого не становится read-only.
- Панель не видит контейнеры: контейнеры должны принадлежать **тому же rootless пользователю**. `sudo podman ps -a` и `podman ps -a` показывают разные наборы.
- Не виден процесс: `podman top` может не поддерживать расширенный формат; сервис пробует упрощённый. Проверьте `podman top ИМЯ pid,ppid,args`. Если процесс завершился, но PID 1 `sleep infinity` остался, панель покажет работающий контейнер без вычислительного кандидата.
- Пустые логи: Podmon показывает только stdout/stderr, доступные через `podman logs`. Логи, которые программа пишет исключительно во внутренний файл, без интеграции с контейнером недоступны.
- Нет host-метрик: установщик мог отключить read-only mounts отдельных `/proc` файлов; контейнерные CPU/RAM при этом продолжают отображаться. Диск относится к файловой системе host `/etc/hostname`.
- Порт недоступен из LAN: проверьте `LAN_IP`, `PublishPort=`, firewall и `curl http://LAN_IP:8080/api/health` с другого компьютера.
- На SELinux-хосте не следует вслепую применять `:Z` к Podman socket: relabel может нарушить доступ владельца. Сначала проверьте UNIX-права и AVC audit.

Podmon не публикует Podman API по TCP. Приватные ключи, токены и пароли сервису не нужны.
