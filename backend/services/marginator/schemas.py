from pydantic import BaseModel, Field

class TableMappingSchema(BaseModel):
    header_row_index: int = Field(
        description="Индекс строки (0-based), где расположены названия колонок"
    )
    product_name_col: str = Field(
        description="Точное название колонки с наименованием товара или артикулом"
    )
    cost_price_col: str = Field(
        description="Точное название колонки с закупочной ценой / себестоимостью"
    )
    selling_price_col: str | None = Field(
        default=None, 
        description="Точное название колонки с ценой продажи (если есть в таблице)"
    )
    commission_col: str | None = Field(
        default=None, 
        description="Точное название колонки с комиссией или логистикой (если есть)"
    )
    quantity_col: str | None = Field(
        default=None, 
        description="Точное название колонки с количеством (если есть)"
    )
