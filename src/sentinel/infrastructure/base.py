"""
Base declarativa de SQLAlchemy sin dependencias de motor.
Evita importaciones circulares entre database.py y orm_models.py
"""
from sqlalchemy.orm import declarative_base

Base = declarative_base()
