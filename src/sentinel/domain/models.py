from pydantic import BaseModel, HttpUrl, Field, field_validator
from datetime import datetime
from typing import Optional

class ServiceTarget(BaseModel):
    """
    Enterprise-grade Service Target model with strict input validation.
    - ID: Auto-increment on INSERT, None for new records
    - Name: 3-100 characters, alphanumeric + spaces/hyphens only
    - URL: Must be valid HTTP/HTTPS URL
    - Check Interval: 5-86400 seconds (DoS protection)
    """
    id: Optional[int] = None
    name: str = Field(..., min_length=3, max_length=100)
    url: HttpUrl
    check_interval: int = Field(default=60, ge=5, le=86400)
    is_active: bool = True
    last_check: Optional[datetime] = None
    status_code: Optional[int] = Field(None, ge=100, le=599)
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate name contains only alphanumeric, spaces, hyphens, underscores."""
        if not all(c.isalnum() or c in ' -_' for c in v):
            raise ValueError('Name must contain only alphanumeric characters, spaces, hyphens, and underscores')
        return v.strip()
    
    @field_validator('url', mode='before')
    @classmethod
    def validate_url_scheme(cls, v):
        """Ensure URL uses HTTP/HTTPS protocol."""
        try:
            # Convert to HttpUrl if it's a string
            if isinstance(v, str):
                from pydantic import HttpUrl as PydanticHttpUrl
                parsed_url = PydanticHttpUrl(v)
                scheme = parsed_url.scheme
            else:
                scheme = v.scheme
            
            if scheme not in ('http', 'https'):
                raise ValueError('URL must use HTTP or HTTPS protocol')
            return v
        except Exception as e:
            raise ValueError(f'Invalid URL: {str(e)}')
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "name": "Google API",
                "url": "https://www.google.com",
                "check_interval": 60,
                "is_active": True,
                "status_code": 200
            }
        }