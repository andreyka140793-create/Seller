"""Экспорт отчёта в .xlsx с цветовой подсветкой маржи и прибыли."""
from __future__ import annotations

import io

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
from openpyxl.utils import get_column_letter


class ExcelExporterService:
    @staticmethod
    def export_results_to_excel(df_results: pd.DataFrame) -> bytes:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_results.to_excel(writer, index=False, sheet_name="Маржинатор_Отчет")

        output.seek(0)
        wb = openpyxl.load_workbook(output)
        ws = wb["Маржинатор_Отчет"]

        header_fill = PatternFill(start_color="064E3B", end_color="064E3B", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

        green_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
        green_font = Font(name="Calibri", color="065F46", bold=True)
        yellow_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
        yellow_font = Font(name="Calibri", color="92400E")
        red_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
        red_font = Font(name="Calibri", color="991B1B", bold=True)
        zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

        thin_border = Border(
            left=Side(style="thin", color="E5E7EB"),
            right=Side(style="thin", color="E5E7EB"),
            top=Side(style="thin", color="E5E7EB"),
            bottom=Side(style="thin", color="E5E7EB"),
        )

        # Заголовки
        ws.row_dimensions[1].height = 30
        for col_num in range(1, ws.max_column + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        def _norm_header(val) -> str:
            return str(val or "").strip().lower().replace("ё", "е")

        margin_cols: list[int] = []
        profit_cols: list[int] = []
        roi_cols: list[int] = []

        for col_num in range(1, ws.max_column + 1):
            name = _norm_header(ws.cell(row=1, column=col_num).value)
            # «Маржинальность %» содержит «маржин», не «маржа»
            if any(x in name for x in ("маржин", "маржа", "margin", "рентаб")):
                margin_cols.append(col_num)
            if any(x in name for x in ("прибыл", "profit", "net")):
                profit_cols.append(col_num)
            if "roi" in name:
                roi_cols.append(col_num)

        highlight_cols = set(margin_cols + profit_cols + roi_cols)

        for row_num in range(2, ws.max_row + 1):
            ws.row_dimensions[row_num].height = 20

            # Берём маржу из первой найденной колонки (если нет — из прибыли)
            margin_val = None
            for mc in margin_cols:
                v = ws.cell(row=row_num, column=mc).value
                if isinstance(v, (int, float)):
                    margin_val = float(v)
                    break
            if margin_val is None:
                for pc in profit_cols:
                    v = ws.cell(row=row_num, column=pc).value
                    if isinstance(v, (int, float)):
                        margin_val = 20.0 if float(v) > 0 else (-1.0 if float(v) < 0 else 0.0)
                        break

            if margin_val is None:
                row_fill = zebra if row_num % 2 == 0 else None
                row_font = None
            elif margin_val >= 20.0:
                row_fill, row_font = green_fill, green_font
            elif margin_val >= 5.0:
                row_fill, row_font = yellow_fill, yellow_font
            else:
                row_fill, row_font = red_fill, red_font

            for col_num in range(1, ws.max_column + 1):
                cell = ws.cell(row=row_num, column=col_num)
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center")

                if col_num in highlight_cols and row_fill is not None:
                    cell.fill = row_fill
                    if row_font is not None:
                        cell.font = row_font
                elif row_num % 2 == 0 and col_num not in highlight_cols:
                    cell.fill = zebra

                # Числовой формат
                if isinstance(cell.value, (int, float)):
                    name = _norm_header(ws.cell(row=1, column=col_num).value)
                    if any(x in name for x in ("%", "маржин", "roi", "проц")):
                        cell.number_format = '0.00'
                    elif any(x in name for x in ("₽", "руб", "цена", "прибыл", "себестоим", "выруч")):
                        cell.number_format = '#,##0.00'

        # Ширина колонок
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.value is not None:
                    max_len = max(max_len, min(len(str(cell.value)), 48))
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        # Зафиксировать шапку
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        final_output = io.BytesIO()
        wb.save(final_output)
        return final_output.getvalue()
