from zoneinfo import ZoneInfo


CITY_TIMEZONES = {
    # India
    "india": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "chennai": "Asia/Kolkata",
    "pune": "Asia/Kolkata",

    # USA
    "new york": "America/New_York",
    "new york city": "America/New_York",
    "chicago": "America/Chicago",
    "houston": "America/Chicago",
    "dallas": "America/Chicago",
    "denver": "America/Denver",
    "phoenix": "America/Phoenix",
    "los angeles": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",

    # Canada
    "toronto": "America/Toronto",
    "vancouver": "America/Vancouver",

    # UK
    "london": "Europe/London",

    # UAE
    "dubai": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai",

    # Australia
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
}


def timezone_for_city(city):
    if not city:
        return None

    city = city.strip().lower()

    # Exact match
    if city in CITY_TIMEZONES:
        return CITY_TIMEZONES[city]

    # Partial match
    for name, timezone_name in CITY_TIMEZONES.items():
        if name in city or city in name:
            return timezone_name

    return None
