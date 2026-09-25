# Установка Podmon: сначала контейнер, затем код, затем запуск

На Ubuntu-хосте используются **только уже имеющиеся Podman, user systemd/loginctl и обычная оболочка**. Не устанавливайте на хост Git, Python, pip, GCC или nginx. Весь Git, SSH client, Python и зависимости Podmon устанавливаются **в подготовительный контейнер**. Репозиторий клонируется **внутри него**. Только после этого контейнер сохраняется как image и запускается мониторинг через rootless Quadlet/systemd.

Работайте от того же Linux-пользователя, которому принадлежат вычислительные rootless-контейнеры. `sudo podman` видит другое хранилище и другие контейнеры.

## 1. Создать подготовительный контейнер на сервере

Подключитесь к серверу тем же способом, которым обычно входите по SSH. Например, если ваш логин на сервере `ivan`, а его адрес `192.168.1.20`, команда выглядит так:

```bash
ssh ivan@192.168.1.20
```

**`ivan` и `192.168.1.20` здесь только примеры:** подставьте свой логин и адрес сервера. Если SSH-соединение уже открыто, повторно подключаться не нужно. Все дальнейшие команды этого раздела выполняются в оболочке сервера от пользователя, чьи rootless-контейнеры нужно мониторить.

Проверьте Podman:

```bash
podman info --format 'rootless={{.Host.Security.Rootless}} cgroup={{.Host.CgroupVersion}}'
podman ps -a
```

Создайте отдельное хранилище для SSH deploy key и сам подготовительный контейнер:

```bash
podman volume create podmon-ssh
podman run -d \
  --name podmon-setup \
  --volume podmon-ssh:/root/.ssh \
  docker.io/library/ubuntu:24.04 \
  sleep infinity
podman inspect podmon-setup --format '{{.State.Status}}'
podman exec -it podmon-setup bash
```

Здесь Podman скачивает **только базовый Ubuntu image в своё хранилище**, что необходимо для создания контейнера. На хост никакие пакеты не устанавливаются. PID 1 `sleep infinity` нужен только для подготовки; веб-сервис ещё не запускается.

## 2. Установить всё необходимое **внутри** `podmon-setup`

Следующие команды выполняются в открывшейся оболочке контейнера:

```bash
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates git openssh-client python3 python3-venv python3-pip
python3 -m venv /opt/podmon-venv
/opt/podmon-venv/bin/pip install \
  fastapi==0.115.12 uvicorn==0.34.2 httpx==0.28.1
mkdir -p /host/proc
touch /host/proc/stat /host/proc/meminfo /host/proc/uptime /host/proc/loadavg /host/hostname
```

Зависимости установлены внутри будущего image. GCC не требуется для этих версий при наличии подходящих Python wheels. На хосте команды `python`, `pip`, `git` и `gcc` не используются.

## 3. Создать ED25519 ключ **внутри** контейнера и клонировать код туда же

Всё ещё внутри `podmon-setup`:

```bash
chmod 700 /root/.ssh
ssh-keygen -t ed25519 -N '' -C podmon-deploy \
  -f /root/.ssh/podmon_deploy_ed25519
chmod 600 /root/.ssh/podmon_deploy_ed25519
chmod 644 /root/.ssh/podmon_deploy_ed25519.pub
cat /root/.ssh/podmon_deploy_ed25519.pub
```

Скопируйте **только публичную строку `.pub`** в GitHub: `bullygen/PodMon → Settings → Deploy keys → Add deploy key`. `Allow write access` оставьте выключенным. Приватный ключ лежит в named volume `podmon-ssh`, **не в файловом слое контейнера**. Этот volume не будет подключён к рабочему podmon. GitHub [описывает этот порядок для deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).

Создайте SSH alias внутри контейнера:

```bash
cat > /root/.ssh/config <<'SSHCONFIG'
Host github-podmon
    HostName github.com
    User git
    IdentityFile /root/.ssh/podmon_deploy_ed25519
    IdentitiesOnly yes
SSHCONFIG
chmod 600 /root/.ssh/config
ssh -T github-podmon
```

При первом соединении сравните показанный fingerprint с [официальными fingerprint GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints) перед подтверждением. Ответ «successfully authenticated» подтверждает ключ; GitHub не предоставляет shell, поэтому `ssh -T` может завершиться кодом 1.

Теперь **внутри контейнера** загрузите код:

```bash
git clone git@github-podmon:bullygen/PodMon.git /srv/podmon
cd /srv/podmon
/opt/podmon-venv/bin/pip check
PYTHONPATH=/srv/podmon/service /opt/podmon-venv/bin/python \
  -m unittest discover -s service/tests -q
exit
```

После `exit` вы снова на хосте. Мониторинг ещё не запущен.

## 4. Сохранить подготовленный контейнер и включить автозапуск

На хосте проверьте `linger`:

```bash
loginctl show-user "$USER" -p Linger
```

Если `Linger=no`, попросите администратора выполнить `loginctl enable-linger ИМЯ_ПОЛЬЗОВАТЕЛЯ` или, если доступен sudo, выполните:

```bash
sudo loginctl enable-linger "$USER"
```

`linger=yes` нужен user systemd, чтобы мониторинг продолжал работать после полного SSH logout и вернулся после reboot. Никакие Git/Python-пакеты для этого на хосте не нужны.

Сначала узнайте **IPv4-адрес сервера в вашей локальной сети**. Выполните на сервере, уже после выхода из `podmon-setup`:

```bash
echo "$SSH_CONNECTION"
```

Если вы вошли на сервер по SSH **из той же локальной сети**, вывод состоит из четырёх частей: `адрес_вашего_компьютера порт адрес_сервера порт`. Например, для строки `192.168.1.50 51432 192.168.1.20 22` адрес сервера — **третья часть, `192.168.1.20`**. Именно этот адрес нужен ниже. Если соединение идёт через VPN, внешний адрес или промежуточный сервер, найдите адрес локального сетевого интерфейса командой:

```bash
ip -4 -brief address show scope global
```

Например, в строке `enp3s0 UP 192.168.1.20/24` адрес — `192.168.1.20` (без `/24`). Выбирайте интерфейс, через который сервер доступен вашим устройствам в LAN; адреса интерфейсов `podman*`, `veth*` и других контейнерных сетей не подходят. Если адрес неясен, используйте тот IPv4-адрес, по которому вы подключаетесь к серверу из этой же сети.

Теперь скопируйте из **подготовленного контейнера** скрипт включения Quadlet и запустите его **на сервере**, передав найденный IP-адрес последним аргументом:

```bash
podman cp podmon-setup:/srv/podmon/service/scripts/activate_host.sh /tmp/podmon-activate-host.sh
bash /tmp/podmon-activate-host.sh 192.168.1.20
```

**Замените только `192.168.1.20` в последней строке на свой адрес сервера.** Остальные символы и команды вводите как показано. Например, если третья часть `SSH_CONNECTION` — `10.0.0.7`, введите `bash /tmp/podmon-activate-host.sh 10.0.0.7`. Число `8080` ниже — порт веб-интерфейса, его заменять не нужно. Адрес, переданный скрипту, привязывает порт мониторинга к сетевому интерфейсу сервера.

Скрипт использует на хосте только Podman, systemd/loginctl и обычные shell-команды. Он проверяет код и тесты **внутри** `podmon-setup`, сохраняет файловый слой через `podman commit --include-volumes=false` в `localhost/podmon:runtime`, проверяет отсутствие приватного ключа в полученном image, создаёт user Quadlet и запускает рабочий `podmon.service`. Ключевой volume исключён из image: [Podman по умолчанию не включает содержимое подключённых volumes в commit](https://docs.podman.io/en/latest/markdown/podman-commit.1.html). После успешного запуска подготовительный контейнер останавливается; volume с ключом остаётся для будущих обновлений.

Рабочий контейнер запускает `uvicorn` как основной процесс, без интерактивного shell. Он не содержит SSH-ключ и не подключает `podmon-ssh`. Через rootless Podman Unix socket он автоматически видит **все контейнеры того же пользователя**. В его конфигурации нет списка контейнеров или путей к расчётам.

Quadlet опубликован только на выбранном адресе сервера и порту `8080`. Если нужен firewall, администратор должен разрешить этот порт **только доверенной LAN-подсети** в уже применяемых сетевых правилах. Не открывайте порт в Интернет и не публикуйте Podman socket по TCP.

## 5. Проверка после запуска, SSH logout и reboot

На хосте:

```bash
loginctl show-user "$USER" -p Linger
systemctl --user status podman.socket podmon.service
podman inspect podmon --format '{{.State.Status}}'
podman healthcheck run podmon
podman exec podmon /opt/podmon-venv/bin/python \
  /srv/podmon/service/scripts/healthcheck.py
```

На компьютере или телефоне **в той же локальной сети** откройте обычный браузер (Firefox, Chrome и т. п.). Введите **в адресную строку** `http://АДРЕС_СЕРВЕРА:8080/`, где `АДРЕС_СЕРВЕРА` — тот же IP, который передали скрипту выше. Например, если запускали `bash /tmp/podmon-activate-host.sh 192.168.1.20`, откройте **`http://192.168.1.20:8080/`**. Используйте `http://`, а не `https://`. После запуска скрипт также печатает готовую строку `Dashboard: http://...:8080` — этот адрес можно скопировать в браузер.

На странице сравните список карточек с `podman ps -a`; поле поиска принимает имя контейнера или часть команды процесса. Новые контейнеры обнаруживаются автоматически.

Затем **полностью** выйдите из SSH (`exit`), подождите несколько минут, откройте панель снова и повторно войдите по SSH. Проверьте `podmon.service`, контейнер и web UI. После согласованного reboot сервера повторите проверку. Это обязательная проверка конкретного сервера; локальные тесты кода её не заменяют.

## 6. Обновление без Git/Python на хосте

На хосте запустите ранее остановленный подготовительный контейнер и войдите в него:

```bash
podman start podmon-setup
podman exec -it podmon-setup bash
```

**Внутри** контейнера:

```bash
cd /srv/podmon
git pull --ff-only
/opt/podmon-venv/bin/pip install -r service/requirements.txt
PYTHONPATH=/srv/podmon/service /opt/podmon-venv/bin/python \
  -m unittest discover -s service/tests -q
exit
```

Затем на хосте снова извлеките актуальный скрипт и примените image. В последней строке снова используйте **тот же адрес сервера**, что при первой установке; `192.168.1.20` ниже — пример:

```bash
podman cp podmon-setup:/srv/podmon/service/scripts/activate_host.sh /tmp/podmon-activate-host.sh
bash /tmp/podmon-activate-host.sh 192.168.1.20
```

## 7. Диагностика

```bash
systemctl --user status podmon.service podman.socket
journalctl --user -u podmon.service -n 100 --no-pager
podman logs --tail 100 podmon
podman ps -a
```

- `git` или `python` не найден на **хосте**: это ожидаемо; команды установки, ключа и `git clone` нужно выполнять внутри `podmon-setup`.
- `Permission denied (publickey)`: ключ и SSH alias проверяйте **внутри** `podmon-setup`; в GitHub добавляется только `.pub`.
- `Linger=no` или podmon умирает после logout: включите linger для rootless пользователя и повторите тест выхода.
- `podmon.service not found`: проверьте `~/.config/containers/systemd/podmon.container` и `systemctl --user daemon-reload`. Quadlet использует `[Install] WantedBy=default.target`; generated service обычно не нужно отдельно `enable`.
- Socket недоступен: проверьте `podman.socket` и `/run/user/$(id -u)/podman/podman.sock`. `:ro` на socket не ограничивает полномочия Podman API; если именно bind с `:ro` не работает на версии Podman, измените только эту строку Quadlet и повторите проверку.
- Панель не видит контейнеры: rootless контейнеры должны принадлежать тому же Linux-пользователю; `sudo podman` показывает другой набор.
- Нет процессов: сервис пробует расширенный и упрощённый `podman top`. `sleep infinity` может жить после окончания вычисления; отсутствие кандидата не доказывает аварию.
- Пустые логи: автоматически доступны только stdout/stderr из `podman logs`, не произвольные файлы внутри других контейнеров.
- Нет host-метрик: отдельные read-only mounts `/proc` могли не пройти проверку; контейнерные CPU/RAM продолжат отображаться.

На хосте остаются только Podman и systemd для создания и автозапуска контейнера. Все пакеты приложения и GitHub-доступ находятся в подготовительном контейнере; приватный ключ не попадает в рабочий image.
