async def main():
    from .app import main as run

    await run()

__all__ = ["main"]
