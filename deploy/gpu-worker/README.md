# Worker GPU Sentinel V1

Le PC GPU exécute uniquement la file Redis `gpu`. Sentinel-core garde PostgreSQL, les scores et les autres files. Aucune donnée métier ne vit sur le PC GPU.

Le worker se connecte à Redis par un tunnel SSH :

```text
127.0.0.1:6380 sur le PC GPU  ->  127.0.0.1:6379 sur sentinel-core
```

Redis du core reste publié sur `127.0.0.1` seulement. Il n'écoute pas sur le LAN.

L'image `sentinel-gpu-worker` est la même sous WSL2 Ubuntu et sous Ubuntu Server. Elle part de CUDA 11.8, compatible avec la GTX 1060 (Pascal) et avec un driver hôte qui supporte CUDA 11.8 ou plus récent. L'image applicative ne suppose pas CUDA 13.

## Fichiers

- `Dockerfile`, `docker-compose.yml` : worker avec le GPU 0, le cache modèles et un healthcheck
- `.env.example` : à copier vers `.env`, jamais versionné
- `install.sh` : installation idempotente
- `tunnel.sh` : `ssh -N -L`
- `check-gpu.sh` : driver, Docker, GPU dans un conteneur
- `healthcheck.sh` : tunnel local et santé du conteneur

Le code du job est dans `app/jobs/gpu/`. Le conteneur ne contient pas le client PostgreSQL de l'application et n'ouvre pas la base.

## Clé SSH

Sur le PC GPU, dans le filesystem Linux :

```bash
ssh-keygen -t ed25519 -f ~/.ssh/sentinel_gpu -N "" -C "sentinel-gpu-worker"
ssh-keyscan -H 192.168.1.116 >> ~/.ssh/known_hosts
ssh-copy-id -i ~/.ssh/sentinel_gpu.pub -p 22 sentinel@192.168.1.116
ssh -i ~/.ssh/sentinel_gpu -o IdentitiesOnly=yes sentinel@192.168.1.116 true
```

`install.sh` crée la clé si elle manque, enregistre `known_hosts` si l'hôte est absent, puis s'arrête avec la commande `ssh-copy-id` tant que la clé n'est pas autorisée. La clé privée reste dans `~/.ssh/`. Elle n'est pas copiée dans Git.

Test du tunnel :

```bash
./tunnel.sh
```

Depuis un autre terminal sur le PC GPU :

```bash
python3 -c 'import socket; socket.create_connection(("127.0.0.1", 6380), 3).close(); print("ok")'
```

Le service utilisateur relance le tunnel :

```bash
systemctl --user enable --now sentinel-gpu-tunnel.service
systemctl --user status sentinel-gpu-tunnel.service
```

Sous WSL2, systemd doit être activé dans `/etc/wsl.conf` (`systemd=true`), puis WSL redémarré. `install.sh` ne modifie pas ce fichier. Sans systemd, il lance `tunnel.sh` en arrière-plan.

## Démarrage

```bash
cp .env.example .env
# AI_MODEL_CACHE=/models doit être un dossier Linux accessible en écriture
./install.sh
./healthcheck.sh
docker compose ps
```

Le conteneur s'appelle `sentinel-gpu-worker`. Il écoute seulement la file `gpu`. Son Redis est forcé sur `127.0.0.1` et `GPU_REDIS_LOCAL_PORT` (6380), même si `.env` contient l'adresse LAN du core.

Présence dans Redis, TTL 120 secondes, renouvelée toutes les 30 secondes :

```text
sentinel:gpu-worker:sentinel-gpu-01
```

## Probe

Avec un JWT obtenu sur le core :

```bash
curl -s -X POST http://192.168.1.116:8000/admin/jobs/gpu-probe \
  -H "Authorization: Bearer $TOKEN"
```

Le job `gpu_system_probe` renvoie un objet avec le nom du GPU, la VRAM totale, la VRAM libre et `cuda_visible`. Le résultat est aussi gardé dans Redis sous `sentinel:gpu-probe:<nom>`. L'écran Administration affiche la présence, le GPU, la VRAM, le heartbeat, le statut et le dernier probe.

## Migration WSL2 vers Ubuntu Server

Aucune donnée métier n'est à copier.

1. Installer Ubuntu Server.
2. Installer le driver NVIDIA, puis vérifier `nvidia-smi`.
3. Installer Docker Engine et le paquet Compose, puis le NVIDIA Container Toolkit.
4. Cloner le dépôt GitHub sur le filesystem Linux, pas sous `/mnt`.
5. Copier le `.env` GPU, ou le recréer depuis `.env.example`.
6. Copier `~/.ssh/sentinel_gpu` ou recréer la clé et autoriser la clé publique sur sentinel-core. Vérifier `known_hosts`.
7. Lancer `deploy/gpu-worker/install.sh`.
8. Vérifier le heartbeat : `GET /admin/gpu-workers` sur le core, ou l'écran Administration.
9. Lancer `POST /admin/jobs/gpu-probe` et vérifier que le résultat décrit le GPU.

Le cache `/models` peut être recopié plus tard. Il est vide en V1.

## Limites V1

- Pas de LLM, pas d'embeddings, pas d'analyse de documents.
- Swagger et le worker GPU sont indépendants. Le worker ne publie pas de port.
- Le tunnel SSH doit rester ouvert. Sans lui, le conteneur devient unhealthy.
- `network_mode: host` est voulu : `localhost:6380` dans le conteneur est le tunnel de l'hôte Linux.
