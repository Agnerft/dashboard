#!/bin/bash
# Script para corrigir o usuario junior no servidor
# Execute no servidor: cd /opt/proj25-dash && bash fix_junior.sh

set -e

echo "=== Atualizando codigo ==="
git pull

echo "=== Rebuildando container ==="
docker compose up -d --build

echo "=== Aguardando container iniciar ==="
sleep 5

echo "=== Obtendo token admin ==="
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8504/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

echo "=== Deletando usuario junior (se existir) ==="
curl -s -X DELETE http://localhost:8504/auth/users/junior \
  -H "Authorization: Bearer $ADMIN_TOKEN" || true

echo "=== Criando usuario junior com multiplas revendas ==="
curl -s -X POST http://localhost:8504/auth/users \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{
    "username": "junior",
    "password": "junior",
    "role": "user",
    "revenda": ["ADS - EVERALDO", "ADS - MICHELI", "ADS - WILLIAM", "ADS - GUILHERME", "ADS - ALEXANDRE", "ADS - JOSE", "ADS - BRUNO", "ADS - DAVID", "ADS - LUCAS"]
  }'

echo ""
echo "=== Testando login do junior ==="
JUNIOR_TOKEN=$(curl -s -X POST http://localhost:8504/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"junior","password":"junior"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

echo "=== Verificando dados do junior ==="
curl -s http://localhost:8504/auth/me \
  -H "Authorization: Bearer $JUNIOR_TOKEN" | python3 -m json.tool

echo ""
echo "=== Concluido! ==="
