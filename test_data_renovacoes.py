# Teste local para verificar se renovações aparecem no frontend
import json

# Simular dados que viriam da API
test_data = {
    "summary": {
        "total_clientes": 1000,
        "ativos_reais": 500,
        "testes_ativos": 50,
        "novos_clientes": 100,
        "vendas": 80,
        "ads_today": 150.50,
        "ads_period": 1200.00,
        "period_start": "2025-04-01",
        "period_end": "2025-04-14",
        "period_label": "14"
    },
    "charts": {
        "ranking": [
            {
                "revenda": "ADS - EVERALDO",
                "vendas": 25,
                "conversoes": 0,
                "testes": 40,
                "renovacoes": 15,  # TESTE: 15 renovações
                "conversao": 62.5,
                "total_clientes": 120,
                "ativos_reais": 80,
                "testes_ativos_reais": 10,
                "novos_clientes": 20,
                "ads_today": 50.00,
                "ads_period": 400.00,
                "sales_total": 40,
                "cost_per_sale": 10.00
            },
            {
                "revenda": "ADS - MICHELI",
                "vendas": 20,
                "conversoes": 0,
                "testes": 30,
                "renovacoes": 10,  # TESTE: 10 renovações
                "conversao": 66.7,
                "total_clientes": 100,
                "ativos_reais": 70,
                "testes_ativos_reais": 8,
                "novos_clientes": 15,
                "ads_today": 40.00,
                "ads_period": 350.00,
                "sales_total": 30,
                "cost_per_sale": 11.67
            },
            {
                "revenda": "REVENDA JOAO",
                "vendas": 15,
                "conversoes": 0,
                "testes": 25,
                "renovacoes": 5,  # TESTE: 5 renovações
                "conversao": 60.0,
                "total_clientes": 80,
                "ativos_reais": 60,
                "testes_ativos_reais": 5,
                "novos_clientes": 12,
                "ads_today": 30.00,
                "ads_period": 250.00,
                "sales_total": 20,
                "cost_per_sale": 12.50
            }
        ]
    },
    "meta": {
        "source": "test",
        "ts": 1776205180
    }
}

print("Dados de teste criados com sucesso!")
print(f"\nTotal de revendas: {len(test_data['charts']['ranking'])}")
print("\nRenovações por revenda:")
for r in test_data['charts']['ranking']:
    print(f"  {r['revenda']}: {r['renovacoes']} renovações")

print(f"\nTotal de renovações: {sum(r['renovacoes'] for r in test_data['charts']['ranking'])}")
