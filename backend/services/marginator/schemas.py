"""Pydantic schemas."""
from pydantic import BaseModel, Field


class TableMappingSchema(BaseModel):
    header_row_index: int = Field(description="0-based header row index")
    product_name_col: str = Field(description="Product name column")
    cost_price_col: str = Field(description="Purchase cost column")
    selling_price_col: str | None = Field(default=None)
    commission_col: str | None = Field(default=None)
    quantity_col: str | None = Field(default=None)
    weight_col: str | None = Field(default=None)
