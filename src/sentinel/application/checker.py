from sentinel.domain.models import ServiceTarget
from sentinel.infrastructure.http_client import HTTPClient
from sentinel.application.notifier import AlertNotifier  # Alerting dependency
from loguru import logger
from datetime import datetime

class StatusChecker:
    def __init__(self):
        self.client = HTTPClient()
        self.notifier = AlertNotifier()  # Inject the notifier

    async def execute(self, target: ServiceTarget) -> ServiceTarget:
        logger.info(f"Monitoring: {target.name} ({target.url})")
        
        status = await self.client.check_status(str(target.url))
        
        target.status_code = status
        target.last_check = datetime.now()
        
        if status == 200:
            logger.success(f"SYSTEM ONLINE: {target.name}")
        else:
            # Trigger the alert path for non-200 responses.
            await self.notifier.notify_failure(target.name, str(target.url), status)
            
        return target