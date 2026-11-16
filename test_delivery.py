"""parse_delivery_date: Amazon's delivery blurbs -> dates.   python test_delivery.py"""
import datetime as dt

from amazon_source import parse_delivery_date as p

T = dt.date(2026, 11, 16)
fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (wanted {want!r})"))


check("single date", p("FREE delivery Thursday, 8 January", T), dt.date(2027, 1, 8))
check("same year", p("FREE delivery Wednesday, 18 November Details", T), dt.date(2026, 11, 18))
check("range uses last day", p("FREE delivery 18 - 20 November", T), dt.date(2026, 11, 20))
check("range with 'to'", p("Delivery 9 to 12 December", T), dt.date(2026, 12, 12))
check("tomorrow", p("FREE delivery Tomorrow, 17 November. Order within 3 hrs", T), dt.date(2026, 11, 17))
check("tomorrow without a date", p("FREE delivery tomorrow", T), dt.date(2026, 11, 17))
check("today", p("Get it today", T), T)
check("nothing", p("", T), None)
check("no date named", p("Currently unavailable", T), None)
check("ordinal", p("delivery Friday 21st November", T), dt.date(2026, 11, 21))
check("year rolls over", p("Delivery 3 February", T), dt.date(2027, 2, 3))
print("FAILS:", fails)
