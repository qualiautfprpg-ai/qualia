import pandas as pd
import numpy as np

entrada = r"C:\Users\gabri\OneDrive\Documentos\Doutorado\Projetos\base_avaliacoes.txt"
saida = r"C:\Users\gabri\OneDrive\Documentos\Doutorado\Projetos\base_avaliacoes_LIMPO.csv"

df = pd.read_csv(entrada, sep=",", encoding="utf-8")

print("\n✅ Arquivo carregado com sucesso!")
print(df.head())


if "Nome" in df.columns:
    df.drop(columns=["Nome"], inplace=True)
    print("✅ Coluna 'Nome' removida com sucesso!")


if "ID" in df.columns:
    df["ID"] = df["ID"].astype(int)
else:
    df.insert(0, "ID", range(1, len(df)+1))
    print("✅ Coluna ID criada automaticamente!")


colunas_numericas = df.columns.drop([
    "Genero", "Classificacao_IMC", "Risco_Comorbidade", "Risco_Cardiovascular",
    "Metabolismo_Basal_Estado", "Estado_Hidratacao", "Intervencao_Prioritaria",
    "Dieta_Recomendada", "Exercicio_Recomendado", "Psicologico_Recomendado",
    "Suplementacao_Recomendada", "Monitoramento_Recomendado",
    "Medicacao_Recomendada"
])

for coluna in colunas_numericas:
    df[coluna] = pd.to_numeric(df[coluna], errors="coerce")

print("✅ Dados numéricos padronizados!")


for coluna in colunas_numericas:
    df[coluna].fillna(df[coluna].median(), inplace=True)

print("✅ Valores nulos numéricos tratados com mediana!")


colunas_texto = df.select_dtypes(include=["object"]).columns

for coluna in colunas_texto:
    df[coluna] = df[coluna].str.lower().str.strip()

print("✅ Dados de texto padronizados!")


if "Altura_m" in df.columns and "Peso_kg" in df.columns:
    df["IMC"] = df["Peso_kg"] / (df["Altura_m"] ** 2)
    df["IMC"] = df["IMC"].round(2)
    print("✅ IMC recalculado com sucesso!")


colunas_normalizacao = [
    "Idade", "Altura_m", "Peso_kg", "Percentual_Gordura",
    "Percentual_Agua", "Percentual_Massa_Muscular",
    "BMR_kcal", "VO2max_mlkgmin", "IMC"
]

for coluna in colunas_normalizacao:
    if coluna in df.columns:
        df[coluna] = (df[coluna] - df[coluna].min()) / (df[coluna].max() - df[coluna].min())

print("✅ Dados normalizados para Machine Learning!")


df.to_csv(saida, index=False, encoding="utf-8")

print("\n✅✅ BASE PRONTA PARA IA!")
print(f"📁 Arquivo salvo em:\n{saida}")
