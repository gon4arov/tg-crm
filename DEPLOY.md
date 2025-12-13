# Deployment (Ubuntu/Debian, systemd)

Инструкции для развёртывания бота на сервере без сторонних зависимостей. Требуется Python 3.9+ и systemd.

1) Создайте отдельного пользователя (без sudo):
```bash
sudo adduser --disabled-password --gecos "" crm-bot
```

2) Скопируйте SSH-ключ для деплоя (пример для локального `~/.ssh/id_rsa.pub`):
```bash
sudo -u crm-bot mkdir -p /home/crm-bot/.ssh
cat ~/.ssh/id_rsa.pub | sudo tee /home/crm-bot/.ssh/authorized_keys
sudo chmod 700 /home/crm-bot/.ssh
sudo chmod 600 /home/crm-bot/.ssh/authorized_keys
sudo chown -R crm-bot:crm-bot /home/crm-bot/.ssh
```

3) Установите код и окружение:
```bash
sudo -u crm-bot mkdir -p /home/crm-bot/app
sudo -u crm-bot git clone https://github.com/gon4arov/tg-crm.git /home/crm-bot/app
sudo -u crm-bot /usr/bin/python3 -m venv /home/crm-bot/app/.venv
sudo -u crm-bot /home/crm-bot/app/.venv/bin/pip install --upgrade pip
# requirements.txt отсутствует, внешние пакеты не требуются; шаг можно пропустить
```

4) Создайте файл `/home/crm-bot/app/.env`:
```bash
cat | sudo tee /home/crm-bot/app/.env >/dev/null <<'EOF'
TELEGRAM_BOT_TOKEN=ваш_токен
# Опционально: KEYCRM_TOKEN=ваш_keycrm_token
# Опционально: ALLOWED_CHAT_IDS=12345,67890
# Опционально: TELEGRAM_FORCE_IPV4=1
# Опционально: TELEGRAM_TIMEOUT_SECONDS=10
# Опционально: TELEGRAM_POLL_TIMEOUT_SECONDS=9
# Опционально: KEYCRM_TIMEOUT_SECONDS=8
EOF
sudo chown crm-bot:crm-bot /home/crm-bot/app/.env
sudo chmod 600 /home/crm-bot/app/.env
```

5) Создайте сервис `systemd` `/etc/systemd/system/crm-bot.service`:
```bash
sudo tee /etc/systemd/system/crm-bot.service >/dev/null <<'EOF'
[Unit]
Description=CRM Telegram Bot
After=network.target

[Service]
User=crm-bot
Group=crm-bot
WorkingDirectory=/home/crm-bot/app
EnvironmentFile=/home/crm-bot/app/.env
ExecStart=/home/crm-bot/app/.venv/bin/python bot.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
```

6) Включите сервис:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now crm-bot.service
sudo systemctl status crm-bot.service --no-pager -n 20
```

7) Логи:
```bash
journalctl -u crm-bot.service -f
```

8) Обновление до последнего `main`:
```bash
sudo -u crm-bot /home/crm-bot/app/.venv/bin/pip install --upgrade pip
cd /home/crm-bot/app
sudo -u crm-bot git fetch --all
sudo -u crm-bot git reset --hard origin/main
sudo systemctl restart crm-bot.service
sudo systemctl status crm-bot.service --no-pager -n 20
```

Если нужна автоматизация обновления от пользователя `crm-bot`, создайте `/home/crm-bot/update-crm-bot.sh` по аналогии с блоком выше и дайте права на перезапуск сервиса через `/etc/sudoers.d/crm-bot-restart`.
