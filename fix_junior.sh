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
ADMIN_RESPONSE=$(curl -s -X POST http://localhost:8504/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}')

echo "Resposta login admin: $ADMIN_RESPONSE"

ADMIN_TOKEN=$(echo "$ADMIN_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))")

if [ -z "$ADMIN_TOKEN" ]; then
    echo "ERRO: Nao foi possivel obter token admin. Verifique a senha."
    exit 1
fi

echo "Token admin obtido com sucesso"

echo "=== Deletando usuario junior (se existir) ==="
curl -s -X DELETE http://localhost:8504/auth/users/junior \
  -H "Authorization: Bearer $ADMIN_TOKEN" || true

echo ""
echo "=== Criando usuario junior com multiplas revendas ==="
CREATE_RESPONSE=$(curl -s -X POST http://localhost:8504/auth/users \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{
    "username": "junior",
    "password": "junior",
    "role": "user",
    "revenda": ["ADS - EVERALDO", "ADS - MICHELI", "ADS - WILLIAM", "ADS - GUILHERME", "ADS - ALEXANDRE", "ADS - JOSE", "ADS - BRUNO", "ADS - DAVID", "ADS - LUCAS"]
  }')

echo "Resposta criacao: $CREATE_RESPONSE"

echo ""
echo "=== Testando login do junior ==="
JUNIOR_RESPONSE=$(curl -s -X POST http://localhost:8504/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"junior","password":"junior"}')

echo "Resposta login junior: $JUNIOR_RESPONSE"

JUNIOR_TOKEN=$(echo "$JUNIOR_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))")

if [ -z "$JUNIOR_TOKEN" ]; then
    echo "ERRO: Nao foi possivel obter token junior"
    exit 1
fi

echo ""
echo "=== Verificando dados do junior ==="
curl -s http://localhost:8504/auth/me \
  -H "Authorization: Bearer $JUNIOR_TOKEN" | python3 -m json.tool

echo ""
echo "=== Concluido! ==="
