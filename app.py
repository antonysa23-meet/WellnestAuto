import os
import json
import re
import requests
from urllib.parse import urlparse, unquote
from flask import Flask, request, jsonify
from dotenv import load_dotenv
 
load_dotenv()
 
app = Flask(__name__)
 
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def call_claude(system_prompt, user_prompt, max_tokens=1000):
    """Helper to call the Anthropic API."""
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    data = response.json()
    return "".join(block.get("text", "") for block in data.get("content", []))
 
 
# -----------------------------------------------------------------------
# 1. LinkedIn info lookup
#
# Profile text is collected manually (someone opens each profile in their own
# logged-in browser and copies the visible text) rather than scraped, since
# automated LinkedIn access violates their ToS and risks account restriction.
# See linkedin_data/collect.py - run once, before deploying, to build up
# linkedin_data/output/<slug>.txt for each profile. Those files are committed
# and deployed with the app; this endpoint only turns already-collected text
# into structured data, and only does so at request time (no bulk pre-processing).
# -----------------------------------------------------------------------
LINKEDIN_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_data", "output")


def _slug_from_linkedin_url(linkedin_url):
    url = linkedin_url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    path = unquote(urlparse(url).path).strip("/")
    slug = path.split("/")[-1] if "/" in path else path
    return re.sub(r"[^a-zA-Z0-9\-]", "-", slug).strip("-").lower()


LINKEDIN_MANIFEST_SCHEMA = """{
  "name": string|null,
  "headline": string|null,
  "location": string|null,
  "about": string|null,
  "experience": [{"title": string, "company": string, "dates": string|null, "location": string|null, "description": string|null}],
  "education": [{"school": string, "degree": string|null, "details": string|null}],
  "skills": [string],
  "honors_awards": [string],
  "posts": [{"text": string, "engagement": string|null}],
  "recommendations": [string],
  "volunteering": [{"role": string, "org": string, "dates": string|null}]
  "extra_info": string|null,
}"""


def _synthesize_linkedin_summary(linkedin_url, raw_text):
    """
    Call Claude once, at request time, to organize a manually-copied raw LinkedIn
    page dump into a consistently-shaped manifest. This must not lose information -
    every role, degree, skill, honor, post, and recommendation present in the raw
    text should end up in the manifest. Only LinkedIn's page chrome (nav, notification
    counts, ads, unrelated "people you may know" suggestions, footer/legal links) is
    dropped, since it isn't about the profile owner at all.
    """
    system_prompt = (
        "You organize a manually-copied LinkedIn profile page into a clean, consistently "
        "structured record. Preserve every substantive fact about the profile owner - all "
        "roles in their work history, all degrees, all skills, all honors/awards, every "
        "distinct post, every recommendation - do not summarize, truncate, or drop any of "
        "it. Only remove page chrome that isn't about the profile owner: navigation menus, "
        "notification counts, ads, 'People you may know' / 'You might like' suggestions for "
        "other people, newsletter subscription promos, footer/legal links, cookie banners. "
        "Never invent information that isn't in the text.\n\n"
        "Respond with ONLY a JSON object matching this exact schema. Always include every "
        "key, even when a section isn't present in the text - use null or [] rather than "
        "omitting the key, and never add extra keys. Respond with the raw JSON object only - "
        "no markdown code fences, no ```json wrapper, no explanation before or after:\n" + LINKEDIN_MANIFEST_SCHEMA
    )
    user_prompt = f"LinkedIn URL: {linkedin_url}\n\nProfile text:\n{raw_text}\n\nReturn the JSON object now."
    raw = call_claude(system_prompt, user_prompt, max_tokens=3000)
    raw_stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        summary = json.loads(raw_stripped)
    except (ValueError, TypeError):
        summary = {
            "name": None, "headline": None, "location": None, "about": raw.strip(),
            "experience": [], "education": [], "skills": [], "honors_awards": [],
            "posts": [], "recommendations": [], "volunteering": [],
        }
    summary["source"] = "manual_raw_text"
    return summary


@app.route("/linkedin_info", methods=["POST"])
def linkedin_info():
    """
    Given a LinkedIn URL, return structured profile info synthesized from
    manually-collected profile text. Pass "raw_text" directly in the request
    body to bypass the lookup (e.g. for a one-off profile outside the original
    batch), or omit it to have the server look up linkedin_data/output/<slug>.txt
    collected ahead of time via linkedin_data/collect.py.
    """
    data = request.get_json()
    linkedin_url = data.get("linkedin_url", "").strip()
    raw_text = (data.get("raw_text") or "").strip()

    if not linkedin_url:
        return jsonify({"error": "linkedin_url is required"}), 400

    slug = _slug_from_linkedin_url(linkedin_url)

    if not raw_text:
        output_path = os.path.join(LINKEDIN_OUTPUT_DIR, f"{slug}.txt")
        if os.path.exists(output_path):
            with open(output_path, encoding="utf-8") as f:
                raw_text = f.read().strip()

    if not raw_text:
        return jsonify({
            "error": "no_raw_text",
            "message": f"No collected profile text for this URL. Run linkedin_data/collect.py "
                       f"to gather it (expected at linkedin_data/output/{slug}.txt), pass it "
                       f"as raw_text, or fill this column in manually."
        }), 404

    summary = _synthesize_linkedin_summary(linkedin_url, raw_text)
    return jsonify({"output": summary})


# -----------------------------------------------------------------------
# 2. Compose personalized email
# -----------------------------------------------------------------------
WELLNEST_CONTEXT = (
    "Wellnest Fertility is a de novo fertility clinic platform opening access to IVF and "
    "fertility care in underserved U.S. markets. Founder/CEO Hannah Johnson has 18 years in "
    "fertility - she was on the founding team that scaled Vios Fertility to 13 clinics in 5 "
    "states, then was Chief Strategy Officer at Kindbody overseeing 26 clinics - working "
    "alongside reproductive endocrinologist Dr. Sarah Bjorkman. Their proof-of-concept clinic in "
    "Ogden, Utah (a 650k-person market that had zero local IVF labs) has grown patient "
    "consultations 67% since launch with a 46% consultation-to-cycle conversion rate. They're "
    "raising $8M to build 2 more clinics and 4 satellite locations in underserved markets. "
    "Downing Capital Group is a current investor in Wellnest."
)

COMPOSE_EMAIL_EXAMPLES = """EXAMPLE 1:
Hi Kathryn, Seems we both have a passion for helping support women! My name is Zina, and I'm a Managing Director at Downing Capital Group, where we invest $1-10M to build early stage businesses ourselves across healthcare, e-commerce, cybersecurity. I saw that you oversee 12 investments, we are in the same range. On our end, we build the businesses from scratch ideating them on our end and hiring a team to build them. One of my favorite investments is Wellnest, a fertility clinic platform opening access to fertility care for women who have historically been overlooked by the system. It's the kind of healthcare work that I imagine would resonate with you, given your focus on building healthy communities in underserved markets and your board involvement with Health in Her Hue. I'd love to connect and compare notes on where we're seeing the most interesting opportunities especially at the intersection of healthcare innovation and access for underrepresented communities. Would you be open to a 20 minute call? Hope to connect soon!

EXAMPLE 2:
Hi Lauren, I came across GHWIN and loved what you're building. Seems like we're in the same space. I'm Zina, Managing Director at Downing Capital Group. We invest $1-10M to build early-stage companies ourselves across healthcare, e-commerce, cybersecurity, and other businesses. We're also operators as we build the businesses ourselves. One of my current favorite investments that seems like it would connect well with the GHWIN network is Wellnest, a fertility clinic platform democratizing access to fertility care for women. It sits squarely in the healthcare access gap you've spoken about, where the system historically hasn't shown up for women. I'd love to swap notes on what you're seeing in women's health, find out if there are portfolio companies in the GHWIN orbit worth knowing, and explore whether there are ways we can be useful to each other. Would you be up for a quick call? All the best, Zina

EXAMPLE 3:
Hi Sonia, Your thesis at 100 Plus Capital that healthspan, not just lifespan, is where the real opportunity lies is one I find deeply compelling. Your work at Portfolia investing in women's health and preventative care maps closely to what we're building. I'm Zina, Managing Director at Downing Capital Group, where we invest $1-10M to build early-stage businesses ourselves across healthcare, e-commerce, cybersecurity, and more. We're operators that build alongside founders. One of my most exciting current investments is Wellnest, a fertility clinic platform opening access to fertility care for women. Fertility sits at a fascinating intersection of healthspan and underserved populations, as we're seeing the effects of delaying family building on women. I'd love to find 20 minutes to compare notes on fertility, on women's health investing, and on what the next few years look like at the intersection of biology and technology. Are you open to connecting? All the best,

EXAMPLE 4:
Hi Shalanda, What you've built with 100KM Ventures is the kind of investor-operator combination I deeply respect - love that you're investing in women at early stages. I'm Zina, Managing Director at Downing Capital Group. We invest $1-10M to build early-stage businesses ourselves across healthcare, e-commerce, cybersecurity, and adjacent sectors. One of my current favorite investments is Wellnest, a fertility clinic platform expanding access to fertility care for women. It's a business I'm personally passionate about - healthcare access for women is long overdue for a rethink, and even cooler, 12 out of the 13 team members are women. I'd love to connect and compare notes on what you're seeing at early stage, share deal learnings, and explore where our perspectives overlap. Would you be open to a quick call? All the best, Zina

EXAMPLE 5:
Hi Shelley, Your work at 11.2 Capital investing in deep tech caught my attention. I love seeing women lead the way in deep tech investments! I'm Zina, Managing Director at Downing Capital Group, where we invest $1-10M to build early-stage companies ourselves across healthcare, e-commerce, cybersecurity, and quite a few other businesses. We're operator investors as we build the businesses ourselves. One of my favorite current investments is Wellnest, a fertility clinic platform opening access to fertility care for women. As technology reshapes what's possible in healthcare delivery, I think there's a compelling intersection with the kind of deep tech bets you're making. I'd love to connect and trade notes on the technologies you're most excited about, on the healthcare opportunities you see, and on where deep tech and care delivery might converge. Would you be open to a 20 minute call? All the best, Zina

EXAMPLE 6:
Hi Carmen, What an awesome name for a venture fund. As a short intro, I'm Zina, Managing Director at Downing Capital Group, previously 3x founder, 2 exits. We invest $1-10M to build early-stage businesses ourselves across healthcare, e-commerce, cybersecurity, and more. We operate alongside our portfolio companies - operators first, investors second. One of my current favorite investments is Wellnest, a fertility clinic platform democratizing access to fertility care for women. I'm particularly passionate about businesses that open doors for women who have been overlooked. I'd love to compare notes on where we're each seeing the most compelling early-stage opportunities, and explore if there's deal flow or perspective worth sharing. Would you be open to a quick call? Warmly, Zina

EXAMPLE 7:
Hi Emily, AIN Ventures and its focus on early-stage companies is close to what we do every day at Downing Capital Group - I'd love to connect and trade perspectives on where we both see the most interesting opportunities right now. I'm Zina, Managing Director at Downing Capital Group, where we invest $1-10M to build early-stage businesses ourselves across healthcare, e-commerce, cybersecurity, and adjacent sectors. In a previous life I was a 3x founder with 2 exits, and now my work is as an investor and operator. One of our most exciting current investments is Wellnest, a fertility clinic platform opening access to fertility care for women. It's the kind of business that's building something structurally important in healthcare, a sector where access has been far too unequal for far too long. We started this business last year and we're already up to 50+ pregnancies, which is pretty awesome. I'd love to swap notes on what we're each seeing at early stage. Would you be open to a short intro call? All the best, Zina"""


@app.route("/compose_email", methods=["POST"])
def compose_email():
    """
    Given full row data + free-text context/instructions, generate a
    personalized cold outreach email in Zina Ajlouny's (Downing Capital Group)
    voice - using Wellnest, a current Downing Capital portfolio investment, as
    a relationship-building hook rather than a direct fundraising ask.
    """
    data = request.get_json()

    required = ["company", "contact_name"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    company = data.get("company", "")
    investor_type = data.get("investor_type", "")
    project = data.get("project", "")
    fit = data.get("fit", "")
    contact_name = data.get("contact_name", "")
    country = data.get("country", "")
    women_angle = data.get("women_angle", "")
    linkedin_info = data.get("linkedin_info", "")
    context = data.get("context", "")  # free-text writing instructions from the Context sheet

    system_prompt = (
        "You are writing as Zina Ajlouny, Managing Director at Downing Capital Group, a firm "
        "that invests $1-10M to build early-stage businesses themselves - operators first, "
        "investors second - across healthcare, e-commerce, cybersecurity, and adjacent sectors. "
        "You're reaching out to another woman (emphasize this aspect) investor/operator to build a relationship, using "
        "Wellnest (a current Downing Capital portfolio investment) as the hook - this is "
        "networking, not a fundraising ask.\n\n"
        f"About Wellnest: {WELLNEST_CONTEXT}\n\n"
        "Follow this formula, matching the voice, length, and structure of the reference emails "
        "below exactly:\n"
        "1. Open with a specific, charasmatic, personalized hook about the recipient - their fund/company, a "
        "stated thesis, a specific portfolio company or board seat, or something notable about "
        "their path. Never generic flattery.\n"
        "2. Briefly introduce yourself as Zina, Managing Director at Downing Capital Group, with "
        "the one-line description of what the firm does.\n"
        "3. Pitch Wellnest as one of your favorite/most exciting current investments, then bridge "
        "it specifically to something about the recipient - their thesis, portfolio, or stated "
        "focus - using the LinkedIn details provided below. "
        "Optionally weave in one concrete Wellnest detail if it fits naturally.\n"
        "4. Close with a soft ask to connect, compare notes , or trade perspectives, plus a request "
        "for a short call.\n"
        "5. Sign off simply (e.g. 'All the best, Zina') - no formal signature block unless the "
        "reference examples show one.\n\n"
        "Match the reference examples' length (roughly 120-220 words) rather "
        "than a fixed word count. Reference specific, concrete details rather than generic, don't use any form of dashes "
        "flattery. Match any additional writing instructions given below exactly.\n\n"
        "If (and only if) you refernce someone from the the team of Wellnest, use their full name and title (e.g. 'reproductive endocrinologist Dr. Sarah Bjorkman') rather than just first name or title.\n\n"
        "Also write a subject line: short (4-8 words), thematic and specific to the actual bridge you "
        "used in this email - not a generic greeting. Style examples: 'Fertility meets healthspan "
        "investing', 'Two women building at early stage', 'Women-led funds, shared thesis', 'Deep tech "
        "meets healthcare access'. No punctuation-heavy clickbait, no 'Quick question'.\n\n"
        f"REFERENCE EXAMPLES (match this voice and structure, with different content):\n{COMPOSE_EMAIL_EXAMPLES}\n\n"
        "The body MUST be broken into paragraphs separated by a blank line, the same way the "
        "reference examples read when displayed (hook paragraph, then the Wellnest/bridge "
        "paragraph, then the closing ask, then the sign-off on its own line) - never one unbroken "
        "block of text. In the JSON string this means literal escaped newlines between paragraphs, "
        "exactly like this shape:\n"
        "{\"subject\": \"Two women building at early stage\", \"body\": \"Hi Addie, <opening "
        "paragraph text>.\\n\\n<Wellnest/bridge paragraph text>.\\n\\n<closing ask paragraph>."
        "\\n\\nAll the best,\\nZina\"}\n\n"
        "Respond with ONLY the JSON object matching that shape. No markdown code fences, no "
        "```json wrapper, no explanation before or after."
    )

    user_prompt = f"""
Write a cold outreach email using this data:

Investor/Company: {company}
Investor type: {investor_type}
Country: {country}
LinkedIn info: {linkedin_info}

Additional project note (if any): {project}
Why this investor is a fit: {fit}
Women's health angle to emphasize: {women_angle}

Contact name: {contact_name}

Writing instructions / context from founder:
{context or "No additional instructions provided - default to the tone of the reference examples."}

Return the JSON object now.
"""

    raw = call_claude(system_prompt, user_prompt, max_tokens=700)
    raw_stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        email = json.loads(raw_stripped)
        if not isinstance(email, dict) or "body" not in email:
            raise ValueError("unexpected shape")
    except (ValueError, TypeError):
        email = {"subject": "Following up", "body": raw.strip()}
    return jsonify({"output": email})
 
 
if __name__ == "__main__":
    app.run(debug=True)