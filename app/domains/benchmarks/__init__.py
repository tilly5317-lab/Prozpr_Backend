"""benchmarks domain — market benchmark index data (catalogue + daily EOD values).

Owns the niftyindices scraper, the backfill/refresh data service, the
thrice-daily EOD scheduler, and the read API. Portfolio analytics (TWR,
portfolio-vs-Nifty) live in the ``portfolio`` domain and read from here.
"""
