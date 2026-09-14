"""One fetcher per source id. Each module exposes pull(source, cfg, archive_dir) -> list[dict]
returning contract rows with verbatim columns filled. The raw file goes to archive_dir."""
