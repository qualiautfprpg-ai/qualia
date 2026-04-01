from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List, Tuple

import json
import numpy as np
import pandas as pd
import joblib

from sklearn.model_selection import (
    train_test_split,
    RandomizedSearchCV,
    KFold,
)
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.ensemble import (
    RandomForestRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
)
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.inspection import permutation_importance

import matplotlib.pyplot as plt

# ============================================================
# CONFIGURAÇÕES GERAIS (fácil de descrever no artigo)
# ============================================================

RANDOM_STATE = 42
TEST_SIZE = 0.20
N_SPLITS = 5  # KFold para CV
N_REPEATS_PERMUTATION = 30  # permutation importance
N_BOOTSTRAP = 2000  # para IC do MAE de teste

# ============================================================
# Funções auxiliares de plot
# ============================================================


def plot_model_mae(results: List[Dict[str, Any]], out_path: Path) -> None:
    model_names = [r["name"] for r in results]
    maes_test = [r["mae_test"] for r in results]

    plt.figure(figsize=(8, 5))
    plt.bar(model_names, maes_test)
    plt.title("Model Comparison – Test MAE")
    plt.xlabel("Model")
    plt.ylabel("Mean Absolute Error (lower is better)")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_model_r2(results: List[Dict[str, Any]], out_path: Path) -> None:
    model_names = [r["name"] for r in results]
    r2_test = [r["r2_test"] for r in results]

    plt.figure(figsize=(8, 5))
    plt.bar(model_names, r2_test)
    plt.title("Model Comparison – Test $R^{2}$")
    plt.xlabel("Model")
    plt.ylabel("$R^{2}$ (higher is better)")
    plt.axhline(y=0.0, linestyle="--", linewidth=1)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_feature_importance(
    feature_names: List[str],
    importances: np.ndarray,
    out_path: Path,
    top_k: int = 10,
) -> None:
    # Garante que é array 1D
    importances = np.asarray(importances)
    sorted_idx = np.argsort(importances)[::-1][:top_k]
    top_features = np.array(feature_names)[sorted_idx]
    top_importances = importances[sorted_idx]

    plt.figure(figsize=(10, 6))
    plt.barh(top_features, top_importances)
    plt.gca().invert_yaxis()
    plt.title("Top Features – Best Model")
    plt.xlabel("Relative Importance")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_predictions_vs_true(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    model_name: str,
    out_path: Path,
) -> None:
    plt.figure(figsize=(6, 6))
    plt.scatter(y_true, y_pred)
    min_val = min(y_true.min(), y_pred.min())
    max_val = max(y_true.max(), y_pred.max())
    plt.plot([min_val, max_val], [min_val, max_val], "r--", label="Ideal line")
    plt.xlabel("True Quality of Life Score")
    plt.ylabel("Predicted Quality of Life Score")
    plt.title(f"Predicted vs True – Best Model ({model_name})")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_residuals_hist(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    model_name: str,
    out_path: Path,
) -> None:
    residuals = y_true - y_pred

    plt.figure(figsize=(10, 5))
    plt.hist(residuals, bins=15, edgecolor="black")
    plt.xlabel("Residual (True - Predicted)")
    plt.ylabel("Frequency")
    plt.title(f"Residuals Distribution – Best Model ({model_name})")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


# ============================================================
# Funções de dados e modelagem
# ============================================================


def load_data(csv_path: Path) -> Tuple[pd.DataFrame, pd.Series, List[str], str]:
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Dataset base_avaliacoes_LIMPO.csv not found at {csv_path}.\n"
            f"Place the CSV in the same directory as this script or adjust the path."
        )

    print(f"[INFO] Reading dataset: {csv_path}")
    df = pd.read_csv(csv_path)

    # Definição de features e alvo
    feature_cols = [
        "Idade",
        "Genero",
        "Altura_m",
        "Peso_kg",
        "Percentual_Gordura",
        "Percentual_Agua",
        "Percentual_Massa_Muscular",
        "BMR_kcal",
        "Idade_Metabolica",
        "Massa_Ossea_kg",
        "VO2max_mlkgmin",
        "Pressao_Sistolica",
        "Pressao_Diastolica",
        "Flexibilidade_cm",
        "Abdominal_rep",
        "Flexao_Braco_rep",
        "FC_Repouso",
        "FC_Pos_Exercicio",
        "FC_Recuperacao_5min",
        "Cooper_km",
        "IMC",
    ]
    target_col = "Qualidade_Vida_Score"

    missing_feats = [c for c in feature_cols if c not in df.columns]
    if missing_feats:
        raise ValueError(f"The following feature columns are missing in the CSV: {missing_feats}")

    if target_col not in df.columns:
        raise ValueError(f"Target column '{target_col}' does not exist in the CSV.")

    # Remove apenas linhas com NaN em features ou alvo
    df = df.dropna(subset=feature_cols + [target_col])

    X = df[feature_cols].copy()
    y = df[target_col].astype(float)

    print(f"[INFO] Total records after NaN removal: {len(df)}")

    return X, y, feature_cols, target_col


def build_preprocessor(feature_cols: List[str]) -> ColumnTransformer:
    categorical_features = ["Genero"]
    numeric_features = [c for c in feature_cols if c not in categorical_features]

    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_features),
            ("num", StandardScaler(), numeric_features),
        ],
        remainder="drop",
    )
    return preprocessor


def get_models_and_spaces() -> Tuple[Dict[str, Any], Dict[str, Dict[str, List[Any]]]]:
    base_models = {
        "RandomForest": RandomForestRegressor(
            random_state=RANDOM_STATE,
            n_jobs=-1,
        ),
        "GradientBoosting": GradientBoostingRegressor(
            random_state=RANDOM_STATE,
        ),
        "HistGradientBoosting": HistGradientBoostingRegressor(
            random_state=RANDOM_STATE,
        ),
    }

    param_distributions = {
        "RandomForest": {
            "model__n_estimators": [200, 300, 400, 600, 800],
            "model__max_depth": [None, 5, 10, 20, 40],
            "model__min_samples_split": [2, 5, 10],
            "model__min_samples_leaf": [1, 2, 4],
            "model__max_features": ["sqrt", "log2", 0.5, 0.8],
            "model__bootstrap": [True, False],
        },
        "GradientBoosting": {
            "model__n_estimators": [100, 200, 300, 500],
            "model__learning_rate": [0.01, 0.05, 0.1, 0.2],
            "model__max_depth": [2, 3, 4, 5],
            "model__subsample": [0.7, 0.8, 1.0],
            "model__min_samples_split": [2, 5, 10],
            "model__min_samples_leaf": [1, 2, 4],
            "model__loss": ["squared_error", "absolute_error"],
        },
        "HistGradientBoosting": {
            "model__learning_rate": [0.01, 0.05, 0.1, 0.2],
            "model__max_depth": [None, 5, 10, 20],
            "model__max_leaf_nodes": [15, 31, 63, 127],
            "model__min_samples_leaf": [10, 20, 30, 50],
            "model__l2_regularization": [0.0, 0.01, 0.1],
        },
    }

    return base_models, param_distributions


def tune_and_evaluate_model(
    name: str,
    base_model: Any,
    preprocessor: ColumnTransformer,
    param_dist: Dict[str, List[Any]],
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> Dict[str, Any]:
    print("\n==============================")
    print(f"Hyperparameter search for: {name}")
    print("==============================")

    pipeline = Pipeline(
        steps=[
            ("preprocess", preprocessor),
            ("model", base_model),
        ]
    )

    if name in ("RandomForest", "HistGradientBoosting"):
        n_iter = 40
    else:  # GradientBoosting
        n_iter = 30

    cv = KFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_STATE)

    search = RandomizedSearchCV(
        estimator=pipeline,
        param_distributions=param_dist,
        n_iter=n_iter,
        scoring="neg_mean_absolute_error",
        n_jobs=-1,
        cv=cv,
        random_state=RANDOM_STATE,
        verbose=1,
    )

    search.fit(X_train, y_train)

    best_pipeline = search.best_estimator_
    best_params = search.best_params_
    best_cv_mae = -search.best_score_

    print(f"\nBest params for {name}:")
    for k, v in best_params.items():
        print(f"  {k}: {v}")
    print(f"Best CV MAE ({name}): {best_cv_mae:.3f}")

    # Avaliação em teste
    y_pred_test = best_pipeline.predict(X_test)
    mae_test = mean_absolute_error(y_test, y_pred_test)
    r2_test = r2_score(y_test, y_pred_test)

    print(f"{name} – Test MAE: {mae_test:.3f} | Test R²: {r2_test:.3f}")

    result = {
        "name": name,
        "pipeline": best_pipeline,
        "best_params": best_params,
        "mae_test": mae_test,
        "r2_test": r2_test,
        "cv_mae_best": best_cv_mae,
        "y_pred_test": y_pred_test,
    }
    return result


def bootstrap_mae_ci(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    n_bootstrap: int = N_BOOTSTRAP,
    alpha: float = 0.05,
) -> Tuple[float, float]:
    rng = np.random.RandomState(RANDOM_STATE)
    n = len(y_true)
    maes = []

    for _ in range(n_bootstrap):
        idx = rng.randint(0, n, size=n)
        mae_bs = mean_absolute_error(y_true[idx], y_pred[idx])
        maes.append(mae_bs)

    lower = np.percentile(maes, 100 * (alpha / 2))
    upper = np.percentile(maes, 100 * (1 - alpha / 2))
    return float(lower), float(upper)


def compute_permutation_feature_importance(
    best_pipeline: Pipeline,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    feature_cols: List[str],
) -> Dict[str, float]:
    print("[INFO] Computing permutation feature importance for the best model...")
    perm = permutation_importance(
        best_pipeline,
        X_test,
        y_test,
        n_repeats=N_REPEATS_PERMUTATION,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    importances = perm.importances_mean
    feature_importance = dict(zip(feature_cols, importances))
    return feature_importance


def generate_all_figures(
    results: List[Dict[str, Any]],
    feature_importance: Dict[str, float],
    y_test: pd.Series,
    y_pred_test_best: np.ndarray,
    best_model_name: str,
    fig_dir: Path,
) -> None:
    print("[INFO] Generating figures...")

    plot_model_mae(results, fig_dir / "fig_model_mae.png")
    plot_model_r2(results, fig_dir / "fig_model_r2.png")

    fi_names = list(feature_importance.keys())
    fi_values = np.array(list(feature_importance.values()))
    plot_feature_importance(fi_names, fi_values, fig_dir / "fig_feature_importance.png", top_k=10)

    plot_predictions_vs_true(
        y_test.values,
        y_pred_test_best,
        best_model_name,
        fig_dir / "fig_predictions_vs_true.png",
    )

    plot_residuals_hist(
        y_test.values,
        y_pred_test_best,
        best_model_name,
        fig_dir / "fig_residuals_hist.png",
    )

    print("[INFO] All figures saved as PNG files.")


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    csv_path = base_dir / "base_avaliacoes_LIMPO.csv"

    # 1) Carregamento de dados
    X, y, feature_cols, target_col = load_data(csv_path)

    # 2) Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
    )
    print(f"[INFO] Train size: {len(X_train)} | Test size: {len(X_test)}")

    # 3) Preprocessamento e modelos
    preprocessor = build_preprocessor(feature_cols)
    base_models, param_distributions = get_models_and_spaces()

    # 4) Hyperparameter tuning + avaliação de cada modelo
    results: List[Dict[str, Any]] = []
    for name, base_model in base_models.items():
        param_dist = param_distributions[name]
        res = tune_and_evaluate_model(
            name=name,
            base_model=base_model,
            preprocessor=preprocessor,
            param_dist=param_dist,
            X_train=X_train,
            y_train=y_train,
            X_test=X_test,
            y_test=y_test,
        )
        results.append(res)

    # 5) Seleciona o melhor modelo (menor MAE em teste)
    best = min(results, key=lambda d: d["mae_test"])

    print("\n================ BEST MODEL OVERALL ================")
    print(f"Model: {best['name']}")
    print(f"Test MAE: {best['mae_test']:.3f} | Test R²: {best['r2_test']:.3f}")
    print(f"Best CV MAE: {best['cv_mae_best']:.3f}")
    print("Best hyperparameters:")
    for k, v in best["best_params"].items():
        print(f"  {k}: {v}")
    print("====================================================\n")

    best_pipeline: Pipeline = best["pipeline"]
    y_pred_test_best: np.ndarray = best["y_pred_test"]

    # 6) Intervalo de confiança do MAE de teste (bootstrap)
    mae_ci_low, mae_ci_high = bootstrap_mae_ci(y_test.values, y_pred_test_best)
    print(f"[INFO] Bootstrap 95% CI for Test MAE: [{mae_ci_low:.3f}, {mae_ci_high:.3f}]")

    # 7) Importância de features (permutation)
    feature_importance = compute_permutation_feature_importance(
        best_pipeline=best_pipeline,
        X_test=X_test,
        y_test=y_test,
        feature_cols=feature_cols,
    )

    # 8) Salva o melhor modelo
    model_path = base_dir / "qualia_model.joblib"
    joblib.dump(
        {
            "pipeline": best_pipeline,
            "feature_cols": feature_cols,
            "target_col": target_col,
            "best_model_name": best["name"],
            "metrics": {
                "mae_test": float(best["mae_test"]),
                "r2_test": float(best["r2_test"]),
                "cv_mae_best": float(best["cv_mae_best"]),
                "mae_test_ci_95": [mae_ci_low, mae_ci_high],
            },
            "best_params": best["best_params"],
            "feature_importance": feature_importance,
            "config": {
                "random_state": RANDOM_STATE,
                "test_size": TEST_SIZE,
                "n_splits_cv": N_SPLITS,
                "n_bootstrap": N_BOOTSTRAP,
                "n_repeats_permutation": N_REPEATS_PERMUTATION,
            },
        },
        model_path,
    )
    print(f"[INFO] Best model saved to: {model_path}")

    # 9) Salva resumo dos resultados em CSV (útil para tabela no artigo)
    results_rows = []
    for r in results:
        results_rows.append(
            {
                "model": r["name"],
                "mae_test": r["mae_test"],
                "r2_test": r["r2_test"],
                "cv_mae_best": r["cv_mae_best"],
                "best_params_json": json.dumps(r["best_params"]),
            }
        )

    results_df = pd.DataFrame(results_rows)
    results_csv_path = base_dir / "results_models.csv"
    results_df.to_csv(results_csv_path, index=False)
    print(f"[INFO] Models summary saved to: {results_csv_path}")

    # 10) Gera todas as figuras
    generate_all_figures(
        results=results,
        feature_importance=feature_importance,
        y_test=y_test,
        y_pred_test_best=y_pred_test_best,
        best_model_name=best["name"],
        fig_dir=base_dir,
    )


if __name__ == "__main__":
    main()
