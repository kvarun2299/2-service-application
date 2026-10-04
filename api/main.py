import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Numeric, String, Text, create_engine, func, select, text
from sqlalchemy.dialects.mysql import TIMESTAMP
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("products-api")


def required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


try:
    database_url = URL.create(
        drivername="mysql+pymysql",
        username=required_env("DB_USER"),
        password=required_env("DB_PASSWORD"),
        host=required_env("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=required_env("DB_NAME"),
        query={"charset": "utf8mb4"},
    )
except ValueError as exc:
    raise RuntimeError("DB_PORT must be a valid TCP port number") from exc

engine = create_engine(
    database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={"connect_timeout": 5},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=func.current_timestamp()
    )


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal
    description: str | None
    created_at: datetime


class HealthRead(BaseModel):
    status: Literal["healthy"]


def get_session():
    with SessionLocal() as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    delays = (1, 2, 4, 8, 16, 30)
    for attempt, delay in enumerate(delays, start=1):
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("Database schema is ready")
            break
        except SQLAlchemyError:
            logger.exception(
                "Unable to initialize the database schema (attempt %d/%d)",
                attempt,
                len(delays),
            )
            if attempt == len(delays):
                raise
            await asyncio.sleep(delay)
    yield
    engine.dispose()


app = FastAPI(title="Products API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("Database operation failed", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Database temporarily unavailable"},
        headers={"Retry-After": "5"},
    )


@app.get("/health", response_model=HealthRead, tags=["health"])
def health() -> HealthRead:
    return HealthRead(status="healthy")


@app.get("/api/health/db", tags=["health"])
def database_health() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Database health check failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
            headers={"Retry-After": "5"},
        ) from exc
    return {"status": "healthy", "database": "connected"}


@app.get("/api/products", response_model=list[ProductRead], tags=["products"])
def list_products(session: SessionDependency) -> list[Product]:
    return list(session.scalars(select(Product).order_by(Product.id)))


@app.post(
    "/api/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    tags=["products"],
)
def create_product(product: ProductCreate, session: SessionDependency) -> Product:
    record = Product(
        name=product.name,
        price=product.price,
        description=product.description,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


@app.delete(
    "/api/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["products"],
)
def delete_product(
    product_id: Annotated[int, Path(gt=0)], session: SessionDependency
) -> Response:
    product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    session.delete(product)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
