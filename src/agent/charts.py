"""
charts.py - Generación de gráficos matplotlib desde DataFrames Gold.
"""

import io
import base64
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker


def _figura_a_base64(fig: plt.Figure) -> str:
    """Convierte figura matplotlib a string base64 PNG."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=130)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return encoded


def _formato_cop(valor, pos=None):
    """Formateador de eje para valores en COP (miles con K/M)."""
    if abs(valor) >= 1_000_000:
        return f"{valor/1_000_000:.1f}M"
    if abs(valor) >= 1_000:
        return f"{valor/1_000:.0f}K"
    return str(int(valor))


def generar_grafico_barras(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    titulo: str = "",
    xlabel: str = "",
    ylabel: str = "",
    color: str = "#4F8EF7",
    horizontal: bool = False,
) -> str:
    """
    Genera un gráfico de barras y retorna imagen en base64.

    Args:
        df: DataFrame con los datos.
        x_col: Columna para el eje X (categorías).
        y_col: Columna para el eje Y (valores).
        titulo: Título del gráfico.
        xlabel / ylabel: Etiquetas de los ejes.
        color: Color de las barras (hex).
        horizontal: Si True, las barras van horizontalmente.

    Returns:
        String base64 de la imagen PNG.
    """
    fig, ax = plt.subplots(figsize=(10, 5))
    x_vals = df[x_col].astype(str)
    y_vals = df[y_col]

    if horizontal:
        bars = ax.barh(x_vals, y_vals, color=color)
        ax.set_xlabel(ylabel or y_col)
        ax.set_ylabel(xlabel or x_col)
        ax.invert_yaxis()
        for bar in bars:
            w = bar.get_width()
            ax.text(w * 1.01, bar.get_y() + bar.get_height() / 2,
                    _formato_cop(w), va="center", fontsize=8)
    else:
        bars = ax.bar(x_vals, y_vals, color=color)
        ax.set_xlabel(xlabel or x_col)
        ax.set_ylabel(ylabel or y_col)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(_formato_cop))
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, h * 1.01,
                    _formato_cop(h), ha="center", va="bottom", fontsize=8)
        plt.xticks(rotation=30, ha="right")

    ax.set_title(titulo, fontsize=13, fontweight="bold", pad=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return _figura_a_base64(fig)


def generar_grafico_lineas(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    titulo: str = "",
    xlabel: str = "",
    ylabel: str = "",
    color: str = "#4F8EF7",
) -> str:
    """Genera un gráfico de líneas y retorna imagen en base64."""
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df[x_col].astype(str), df[y_col], marker="o", color=color,
            linewidth=2, markersize=5)
    ax.fill_between(df[x_col].astype(str), df[y_col], alpha=0.12, color=color)
    ax.set_xlabel(xlabel or x_col)
    ax.set_ylabel(ylabel or y_col)
    ax.set_title(titulo, fontsize=13, fontweight="bold", pad=12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_formato_cop))
    plt.xticks(rotation=30, ha="right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return _figura_a_base64(fig)


def generar_grafico_pie(
    df: pd.DataFrame,
    label_col: str,
    value_col: str,
    titulo: str = "",
) -> str:
    """Genera un gráfico circular y retorna imagen en base64."""
    fig, ax = plt.subplots(figsize=(8, 6))
    labels = df[label_col].astype(str)
    values = df[value_col]
    colors = plt.cm.Set2.colors[:len(labels)]  # type: ignore

    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        colors=colors,
        startangle=140,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
    )
    for t in autotexts:
        t.set_fontsize(9)
    ax.set_title(titulo, fontsize=13, fontweight="bold", pad=16)
    fig.tight_layout()
    return _figura_a_base64(fig)


def auto_grafico(
    df: pd.DataFrame,
    chart_type: str,
    titulo: str = "",
) -> str | None:
    """
    Intenta generar automáticamente el gráfico más adecuado.

    Detecta columnas numéricas y categóricas. Retorna None si
    el DataFrame no tiene estructura compatible.
    """
    if df is None or df.empty or len(df.columns) < 2:
        return None

    num_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(exclude="number").columns.tolist()

    if not num_cols:
        return None

    y_col = num_cols[0]
    x_col = cat_cols[0] if cat_cols else df.columns[0]

    try:
        if chart_type == "pie" and len(df) <= 10:
            return generar_grafico_pie(df, x_col, y_col, titulo)
        elif chart_type == "line":
            return generar_grafico_lineas(df, x_col, y_col, titulo=titulo)
        else:
            horizontal = len(df) > 6
            return generar_grafico_barras(df, x_col, y_col, titulo=titulo,
                                          horizontal=horizontal)
    except Exception:
        return None
