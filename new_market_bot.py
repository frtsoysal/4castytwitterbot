#!/usr/bin/env python3
"""
Polymarket New Event Bot
========================

Polls Gamma API /events endpoint every 10 minutes.
Finds events created in the last 10 minutes.
Tweets the one with highest volume.

Features:
- Uses /events endpoint for accurate aggregated volume/liquidity
- Only fetches events created in last 10 minutes
- Filters out sports and crypto spam
- Tweets highest volume new event

Usage:
    python new_market_bot.py

Environment Variables Required:
    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
"""

import urllib.request
import urllib.parse
import json
import ssl
import os
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Tuple
import tempfile

# Optional: Twitter API
try:
    import tweepy
    TWEEPY_AVAILABLE = True
except ImportError:
    TWEEPY_AVAILABLE = False
    print("⚠️  tweepy not installed. Run: pip install tweepy")

# =============================================================================
# CONFIGURATION
# =============================================================================

# SSL bypass for API calls
ssl._create_default_https_context = ssl._create_unverified_context

# Paths
SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "bot_state.json"
LOG_FILE = SCRIPT_DIR / "bot.log"

# API
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# Polling settings
POLL_INTERVAL_SECONDS = 600  # 10 minutes
LOOKBACK_MINUTES = 10  # Look for events created in last 10 minutes

# Filters
MIN_VOLUME = 0  # Minimum total volume to tweet ($0 = no filter)

# Sports series slugs to filter out
SPORTS_SERIES = [
    "nba", "nfl", "nhl", "mlb", "mls", "wnba",
    "nba-2026", "nfl-2025", "nhl-2026", "cfb", "cfb-2025",
    "premier-league", "premier-league-2025", "bundesliga", "bundesliga-2025",
    "la-liga", "serie-a", "ligue-1", "champions-league", "europa-league",
    "ucl-2025", "uel-2025",
]

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def load_state() -> dict:
    """Load bot state from JSON file."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load state: {e}")
    return {
        "tweeted_event_ids": [],
        "total_tweets_sent": 0,
        "last_poll_time": None
    }


def save_state(state: dict):
    """Save bot state to JSON file."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Could not save state: {e}")


# =============================================================================
# GAMMA API - EVENTS
# =============================================================================

def fetch_recent_events(lookback_minutes: int = LOOKBACK_MINUTES) -> List[Dict]:
    """
    Fetch events created in the last N minutes from /events endpoint.
    """
    # Calculate cutoff time
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    params = {
        "limit": 100,
        "order": "createdAt",
        "ascending": "false",
        "closed": "false",
    }
    
    url = f"{GAMMA_API_BASE}/events?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (PolymarketBot/1.0)")
        req.add_header("Accept", "application/json")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            events = json.loads(resp.read().decode())
            if not isinstance(events, list):
                return []
            
            # Filter to only events created after cutoff
            recent = []
            for event in events:
                created_at = event.get("createdAt", "")
                if created_at >= cutoff_str:
                    recent.append(event)
                else:
                    # Events are sorted by createdAt desc, so we can stop
                    break
            
            return recent
            
    except Exception as e:
        logger.error(f"Gamma API error: {e}")
        return []


# =============================================================================
# FILTERS
# =============================================================================

def is_sports_event(event: Dict) -> bool:
    """Check if event is sports-related."""
    # Check series
    series = event.get("series", [])
    for s in series:
        slug = (s.get("slug") or "").lower()
        if slug in SPORTS_SERIES:
            return True
        # Also check for common sports patterns
        if any(x in slug for x in ["nba", "nfl", "nhl", "mlb", "soccer", "football", "basketball"]):
            return True
    
    # Check title patterns
    title = (event.get("title") or "").lower()
    sports_patterns = [" vs ", " vs. ", "o/u ", "spread:", "moneyline", "over/under"]
    if any(p in title for p in sports_patterns):
        return True
    
    return False


def is_crypto_spam(event: Dict) -> bool:
    """Check if event is crypto price prediction spam."""
    title = (event.get("title") or "").lower()
    if "up or down" in title:
        return True
    return False


def is_expired(event: Dict) -> bool:
    """Check if event's endDate has passed."""
    end_date = event.get("endDate")
    if not end_date:
        return False
    try:
        if end_date.endswith('Z'):
            end_date = end_date[:-1] + '+00:00'
        end_dt = datetime.fromisoformat(end_date)
        return end_dt < datetime.now(timezone.utc)
    except Exception:
        return False


def filter_events(events: List[Dict], state: dict) -> List[Dict]:
    """
    Filter events to quality ones only.
    Excludes: sports, crypto spam, expired, already tweeted.
    """
    tweeted_ids = set(state.get("tweeted_event_ids", []))
    
    filtered = []
    for event in events:
        event_id = str(event.get("id", ""))
        
        # Skip already tweeted
        if event_id in tweeted_ids:
            logger.debug(f"Skipping already tweeted: {event_id}")
            continue
        
        # Skip sports
        if is_sports_event(event):
            logger.debug(f"Skipping sports: {event.get('title', '')[:40]}")
            continue
        
        # Skip crypto spam
        if is_crypto_spam(event):
            logger.debug(f"Skipping crypto spam: {event.get('title', '')[:40]}")
            continue
        
        # Skip expired
        if is_expired(event):
            logger.debug(f"Skipping expired: {event.get('title', '')[:40]}")
            continue
        
        # Check volume threshold
        volume = float(event.get("volume", 0) or 0)
        if volume < MIN_VOLUME:
            logger.debug(f"Skipping low volume: {event.get('title', '')[:40]}")
            continue
        
        filtered.append(event)
    
    return filtered


def find_best_event(events: List[Dict]) -> Optional[Dict]:
    """Find event with highest volume."""
    if not events:
        return None
    
    # Sort by volume descending
    events.sort(key=lambda e: float(e.get("volume", 0) or 0), reverse=True)
    return events[0]


# =============================================================================
# TWEET FORMATTING
# =============================================================================

def format_number(num: float) -> str:
    """Format number with K/M suffix."""
    if num >= 1_000_000:
        return f"${num/1_000_000:.1f}M"
    elif num >= 1_000:
        return f"${num/1_000:.0f}K"
    else:
        return f"${num:.0f}"


def format_tweet(event: Dict) -> str:
    """
    Format event data into a tweet.
    Max 280 characters.
    """
    title = event.get("title") or "New Event"
    slug = event.get("slug") or event.get("id")
    url = f"https://polymarket.com/event/{slug}"
    
    # Get financial info
    volume = float(event.get("volume", 0) or 0)
    liquidity = float(event.get("liquidity", 0) or 0)
    
    vol_str = format_number(volume)
    liq_str = format_number(liquidity)
    
    # Build tweet with 4Casty CTA
    tweet = (
        f"🚨 New Polymarket Event!\n\n"
        f"{title}\n\n"
        f"📊 Volume: {vol_str}\n"
        f"💰 Liquidity: {liq_str}\n\n"
        f"Trade 👉 {url}\n\n"
        f"🔮 Join @4Castylabs waitlist now: www.4casty.com"
    )
    
    # Truncate title if tweet is too long
    if len(tweet) > 280:
        max_title_len = len(title) - (len(tweet) - 280) - 3
        if max_title_len > 20:
            title = title[:max_title_len] + "..."
            tweet = (
                f"🚨 New Polymarket Event!\n\n"
                f"{title}\n\n"
                f"📊 Volume: {vol_str}\n"
                f"💰 Liquidity: {liq_str}\n\n"
                f"Trade 👉 {url}\n\n"
                f"🔮 Join 4Casty Terminal waitlist: www.4casty.com"
            )
    
    return tweet[:280]


# =============================================================================
# IMAGE HANDLING
# =============================================================================

def get_event_image_url(event: Dict) -> Optional[str]:
    """Get image URL from event data."""
    for field in ["image", "coverImage", "icon"]:
        url = event.get(field)
        if url and isinstance(url, str) and url.startswith("http"):
            return url
    return None


def download_image(url: str) -> Optional[bytes]:
    """Download image from URL."""
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except Exception as e:
        logger.warning(f"Could not download image: {e}")
        return None


# =============================================================================
# TWITTER API
# =============================================================================

def get_twitter_clients() -> Tuple[Optional['tweepy.Client'], Optional['tweepy.API']]:
    """
    Initialize Twitter API clients.
    Returns (v2_client, v1_api) - v1 is needed for media upload.
    """
    if not TWEEPY_AVAILABLE:
        logger.warning("tweepy not available")
        return None, None
    
    api_key = os.environ.get("X_API_KEY")
    api_secret = os.environ.get("X_API_SECRET")
    access_token = os.environ.get("X_ACCESS_TOKEN")
    access_secret = os.environ.get("X_ACCESS_SECRET")
    
    if not all([api_key, api_secret, access_token, access_secret]):
        logger.warning("Twitter credentials not set")
        return None, None
    
    try:
        # V2 client for tweeting
        v2_client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret
        )
        
        # V1 API for media upload
        auth = tweepy.OAuth1UserHandler(
            api_key, api_secret,
            access_token, access_secret
        )
        v1_api = tweepy.API(auth)
        
        return v2_client, v1_api
    except Exception as e:
        logger.error(f"Could not initialize Twitter clients: {e}")
        return None, None


def upload_image(v1_api: 'tweepy.API', image_data: bytes) -> Optional[str]:
    """Upload image to Twitter and return media_id."""
    if not v1_api:
        return None
    
    try:
        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_data)
            temp_path = f.name
        
        # Upload
        media = v1_api.media_upload(filename=temp_path)
        
        # Cleanup
        Path(temp_path).unlink(missing_ok=True)
        
        logger.info(f"📷 Image uploaded, media_id: {media.media_id}")
        return str(media.media_id)
        
    except Exception as e:
        logger.warning(f"Media upload failed: {e}")
        return None


def send_tweet_with_image(
    v2_client: 'tweepy.Client',
    v1_api: Optional['tweepy.API'],
    text: str,
    image_url: Optional[str] = None
) -> bool:
    """Send a tweet with optional image. Returns True on success."""
    if not v2_client:
        img_info = f" [with image]" if image_url else ""
        logger.info(f"[DRY RUN] Would tweet{img_info}:\n{text}\n")
        return True
    
    try:
        media_ids = None
        
        # Handle image upload
        if image_url and v1_api:
            image_data = download_image(image_url)
            if image_data:
                media_id = upload_image(v1_api, image_data)
                if media_id:
                    media_ids = [media_id]
        
        # Send tweet
        if media_ids:
            response = v2_client.create_tweet(text=text, media_ids=media_ids)
        else:
            response = v2_client.create_tweet(text=text)
        
        tweet_id = response.data.get('id') if response.data else 'unknown'
        logger.info(f"✅ Tweet sent! ID: {tweet_id}")
        return True
        
    except tweepy.TooManyRequests:
        logger.warning("Rate limited! Waiting 15 minutes...")
        time.sleep(15 * 60)
        return False
    except tweepy.TwitterServerError as e:
        logger.error(f"Twitter server error: {e}")
        return False
    except Exception as e:
        logger.error(f"Tweet failed: {e}")
        return False


# =============================================================================
# MAIN BOT LOOP
# =============================================================================

def run_once(
    v2_client: Optional['tweepy.Client'],
    v1_api: Optional['tweepy.API'],
    state: dict
) -> dict:
    """
    Single polling iteration.
    Fetches events from last 10 minutes, tweets the best one with image.
    """
    logger.info(f"🔍 Polling for events created in last {LOOKBACK_MINUTES} minutes...")
    
    # Fetch recent events
    events = fetch_recent_events(LOOKBACK_MINUTES)
    if not events:
        logger.info("No new events in timeframe")
        state["last_poll_time"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return state
    
    logger.info(f"Found {len(events)} events in last {LOOKBACK_MINUTES} min")
    
    # Filter to quality events
    quality_events = filter_events(events, state)
    
    if not quality_events:
        logger.info("No quality events after filtering")
        state["last_poll_time"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return state
    
    logger.info(f"🆕 {len(quality_events)} quality event(s) found!")
    
    # Find best event (highest volume)
    best_event = find_best_event(quality_events)
    
    if not best_event:
        logger.info("No event selected")
        return state
    
    event_id = str(best_event.get("id", ""))
    title = best_event.get("title", "")[:50]
    volume = float(best_event.get("volume", 0) or 0)
    
    logger.info(f"🏆 Best event: {title}... (Vol: ${volume:,.0f})")
    
    # Get event image
    image_url = get_event_image_url(best_event)
    if image_url:
        logger.info(f"📷 Found image: {image_url[:60]}...")
    
    # Tweet it with image
    tweet_text = format_tweet(best_event)
    success = send_tweet_with_image(v2_client, v1_api, tweet_text, image_url)
    
    if success:
        # Mark as tweeted
        tweeted_ids = state.get("tweeted_event_ids", [])
        tweeted_ids.append(event_id)
        # Keep only last 500 IDs
        state["tweeted_event_ids"] = tweeted_ids[-500:]
        state["total_tweets_sent"] = state.get("total_tweets_sent", 0) + 1
        save_state(state)
    
    state["last_poll_time"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    
    return state


def main():
    """Main bot entry point."""
    logger.info("=" * 60)
    logger.info("🚀 POLYMARKET NEW EVENT BOT STARTING")
    logger.info("=" * 60)
    
    # Load state
    state = load_state()
    logger.info(f"State loaded. Total tweets sent: {state.get('total_tweets_sent', 0)}")
    
    # Initialize Twitter clients (v2 for tweets, v1 for media upload)
    v2_client, v1_api = get_twitter_clients()
    if v2_client:
        logger.info("✅ Twitter client initialized")
        if v1_api:
            logger.info("✅ Image upload enabled")
    else:
        logger.warning("⚠️  Running in DRY RUN mode (no Twitter credentials)")
    
    # Configuration summary
    logger.info(f"Poll interval: {POLL_INTERVAL_SECONDS}s ({POLL_INTERVAL_SECONDS//60} min)")
    logger.info(f"Lookback window: {LOOKBACK_MINUTES} minutes")
    logger.info(f"Min volume: ${MIN_VOLUME}")
    logger.info(f"Filters: Sports + Crypto spam excluded")
    logger.info(f"Strategy: Tweet highest volume new event with image")
    
    # Main loop
    logger.info("\n🔁 Starting polling loop...\n")
    
    try:
        while True:
            try:
                state = run_once(v2_client, v1_api, state)
            except Exception as e:
                logger.error(f"Error in poll cycle: {e}")
            
            logger.info(f"💤 Sleeping {POLL_INTERVAL_SECONDS}s ({POLL_INTERVAL_SECONDS//60} min)...")
            time.sleep(POLL_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        logger.info("\n👋 Bot stopped by user")
        save_state(state)


if __name__ == "__main__":
    main()
