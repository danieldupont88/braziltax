"""
brasiltax — Brazilian IRPF calculator from B3 negociacao reports.

Usage:
    python main.py [reports_dir]

    reports_dir defaults to ./reports

Place your negociacao-*.xlsx files in reports_dir.
"""

import sys
import os
import calendar

import pandas as pd
from rich.console import Console
from rich.table import Table
from rich import box
from rich.rule import Rule

from parsers.negociacao import load_all_negociacao
from tax.calculator import compute_gains, results_to_df, positions_to_df

console = Console()
MONTH_NAMES = [calendar.month_abbr[i] for i in range(1, 13)]


def _r(value: float) -> str:
    return f"R$ {value:,.2f}"


def _gain_color(value: float) -> str:
    if value > 0:
        return f"[green]{_r(value)}[/green]"
    if value < 0:
        return f"[red]{_r(value)}[/red]"
    return _r(value)


def print_monthly_table(df: pd.DataFrame) -> None:
    if df.empty:
        return

    console.print(Rule("[bold cyan]Ganhos e Impostos por Mês[/bold cyan]"))

    for year in sorted(df["Ano"].unique()):
        year_df = df[df["Ano"] == year]
        t = Table(title=str(int(year)), box=box.SIMPLE_HEAVY, show_lines=True)
        t.add_column("Mês", style="cyan", justify="center")
        t.add_column("Tipo", style="magenta")
        t.add_column("Vendido (R$)", justify="right")
        t.add_column("Ganho Bruto", justify="right")
        t.add_column("Prej. Abatido", justify="right", style="dim")
        t.add_column("Ganho Tributável", justify="right")
        t.add_column("Imposto DARF", justify="right")
        t.add_column("Situação", justify="center")

        year_tax = 0.0
        for _, row in year_df.sort_values(["Mês", "Tipo de Ativo"]).iterrows():
            tax = row["Imposto Devido (R$)"]
            year_tax += tax

            if row["Isento"]:
                status = "[dim]Isento[/dim]"
            elif row["Ganho Bruto (R$)"] < 0:
                status = "[yellow]Prejuízo[/yellow]"
            elif tax > 0:
                status = f"[red]DARF {_r(tax)}[/red]"
            else:
                status = "[green]OK[/green]"

            t.add_row(
                MONTH_NAMES[int(row["Mês"]) - 1],
                row["Tipo de Ativo"],
                _r(row["Total Vendido (R$)"]),
                _gain_color(row["Ganho Bruto (R$)"]),
                _r(row["Prejuízo Abatido (R$)"]) if row["Prejuízo Abatido (R$)"] > 0 else "-",
                _gain_color(row["Ganho Tributável (R$)"]),
                f"[red]{_r(tax)}[/red]" if tax > 0 else "-",
                status,
            )

        t.add_row(
            "", "[bold]Total[/bold]", "", "", "", "",
            f"[bold red]{_r(year_tax)}[/bold red]" if year_tax > 0 else "[bold]-[/bold]",
            "",
        )
        console.print(t)


def print_yearly_summary(df: pd.DataFrame) -> None:
    if df.empty:
        return

    console.print(Rule("[bold yellow]Resumo Anual[/bold yellow]"))
    t = Table(box=box.SIMPLE_HEAVY, show_lines=True)
    t.add_column("Ano", style="cyan", justify="center")
    t.add_column("Tipo", style="magenta")
    t.add_column("Total Vendido (R$)", justify="right")
    t.add_column("Ganho Líquido (R$)", justify="right")
    t.add_column("Total DARF (R$)", style="red", justify="right")

    for year in sorted(df["Ano"].unique()):
        year_df = df[df["Ano"] == year]
        for asset_type, grp in year_df.groupby("Tipo de Ativo"):
            t.add_row(
                str(int(year)),
                asset_type,
                _r(grp["Total Vendido (R$)"].sum()),
                _gain_color(grp["Ganho Bruto (R$)"].sum()),
                _r(grp["Imposto Devido (R$)"].sum()),
            )

    console.print(t)


def print_positions(df_pos: pd.DataFrame) -> None:
    if df_pos.empty:
        console.print("[dim]Sem posições abertas.[/dim]")
        return

    console.print(Rule("[bold blue]Posições Atuais (Preço Médio Ponderado)[/bold blue]"))
    t = Table(box=box.SIMPLE_HEAVY, show_lines=True)
    t.add_column("Tipo", style="magenta", justify="center")
    t.add_column("Ticker", style="cyan")
    t.add_column("Quantidade", justify="right")
    t.add_column("Preço Médio (R$)", justify="right")
    t.add_column("Custo Total (R$)", style="yellow", justify="right")

    for asset_type, grp in df_pos.groupby("Tipo de Ativo"):
        for _, row in grp.iterrows():
            t.add_row(
                asset_type,
                row["Ticker"],
                f"{row['Quantidade']:.0f}",
                _r(row["Preço Médio (R$)"]),
                _r(row["Custo Total (R$)"]),
            )

    console.print(t)


def print_next_steps(df: pd.DataFrame) -> None:
    current_irpf_year = (
        pd.Timestamp.now().year
        if pd.Timestamp.now().month > 5
        else pd.Timestamp.now().year - 1
    )

    console.print(Rule("[bold]Próximos Passos — IRPF[/bold]"))
    console.print(
        f"[bold]Ano-base IRPF:[/bold] {current_irpf_year} "
        f"(declaração IRPF{current_irpf_year + 1})\n"
    )
    console.print(
        "[bold]1. DARF mensal (Renda Variável):[/bold]\n"
        "   Cada linha com 'Imposto Devido' acima representa um DARF a pagar até o último dia útil do mês seguinte.\n"
        "   Código DARF:\n"
        "   • 6015 — Ações (mercado à vista)\n"
        "   • 6015 — BDR e ETF\n"
        "   • 6015 — FII (ganho de capital na alienação de cotas)\n"
    )
    console.print(
        "[bold]2. Bens e Direitos — Renda Variável (Grupo 03):[/bold]\n"
        "   Use a tabela 'Posições Atuais' para preencher o Custo de Aquisição em 31/12.\n"
        "   O valor declarado é o [yellow]custo total (preço médio × quantidade)[/yellow], não o valor de mercado.\n"
    )
    console.print(
        "[bold]3. Operações Comuns / Day-Trade:[/bold]\n"
        "   Este relatório não distingue day-trade (alíquota 20%).\n"
        "   Verifique operações no mesmo dia manualmente nas Notas de Corretagem detalhadas.\n"
    )
    console.print(
        "[bold red]ATENÇÃO:[/bold red] Resultados são estimativas. Audite com um contador antes de declarar.\n"
        "Os cálculos não incluem custos operacionais (corretagem, emolumentos) que reduzem o ganho tributável.\n"
    )


def main() -> None:
    reports_dir = sys.argv[1] if len(sys.argv) > 1 else "reports"

    if not os.path.isdir(reports_dir):
        console.print(
            f"[red]Diretório '{reports_dir}' não encontrado.[/red]\n"
            "Crie a pasta e coloque seus arquivos negociacao-*.xlsx nela.\n"
            "Uso: python main.py [caminho/para/relatorios]"
        )
        sys.exit(1)

    console.print(f"\n[bold]brasiltax[/bold] — lendo negociações de [cyan]{reports_dir}[/cyan]\n")

    df_trades = load_all_negociacao(reports_dir)

    years = sorted(df_trades["Data"].dt.year.unique())
    console.print(f"Operações encontradas: [cyan]{len(df_trades)}[/cyan] trades "
                  f"| Anos: [cyan]{', '.join(str(y) for y in years)}[/cyan]\n")

    results, states = compute_gains(df_trades)
    df_results = results_to_df(results)
    df_positions = positions_to_df(states)

    print_yearly_summary(df_results)
    print_monthly_table(df_results)
    print_positions(df_positions)
    print_next_steps(df_results)


if __name__ == "__main__":
    main()
