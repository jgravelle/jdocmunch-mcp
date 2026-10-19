"""OCR regression check for the [ocr] extra: round 3's 12 real scans.

A manual pre-release check, not CI: it needs the network and a real OCR engine.

    PYTHONPATH=src python scripts/ocr_regression/ocr_regression.py

It fetches each public-domain page image at run time into a temporary folder,
refuses any whose SHA-256 differs from the manifest below, OCRs it with the
engine jDocMunch's ``auto`` detection picks (or ``JDOCMUNCH_OCR_ENGINE``), and
scores it against ground truth. Wikisource ground truth is fetched at a pinned
revision id and converted by round 3's frozen rules; the three hand-typed
pages are our own transcriptions and are embedded here. No image is stored.

Scoring is round 1's (CER on whitespace-normalised text, word recall on
lower-cased, punctuation-stripped words) with round 3's dot-leader rule.

Exit status 1 when the 6 prose pages without type specimens fall below the
shipping bar (mean word recall 0.95, CER 0.05; J, 2026-10-19). Decorative and
black-letter type (amp61, amp62) and forms are reported, never gated.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import statistics
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

UA = {"User-Agent": "jdocmunch-ocr-regression/1.0 (https://github.com/jgravelle/jdocmunch-mcp)"}
WS_API = "https://en.wikisource.org/w/api.php"

#: The shipping bar, on the PROSE pages only.
PROSE = ("eb5_300", "eb5_700", "eb10_300", "eb10_700", "amp60", "amp63")
BAR_RECALL, BAR_CER = 0.95, 0.05
#: Measured at release (Tesseract 5.4.0, Windows 11, 2026-10-19), for comparison.
SHIPPED_BASELINE = {"tesseract": (0.9744, 0.020), "windows": (0.9709, 0.020)}

MANIFEST = [{'id': 'form12',
  'kind': 'form',
  'image_url': 'https://archive.org/download/formssupplements00stanrich/page/n12.jpg',
  'image_sha256': '45dc331fe192b2d0ab81d662221d2b828ee2ff4d6398c1d6c2bdfc87dca79b23',
  'licence': 'NOT_IN_COPYRIGHT',
  'source': 'https://archive.org/details/formssupplements00stanrich',
  'truth_sha256': 'fc83e757dcfa1cb8a26247e03b6df22bddb192706a0fe426667ef03f7be35b82'},
 {'id': 'form16',
  'kind': 'form',
  'image_url': 'https://archive.org/download/formssupplements00stanrich/page/n16.jpg',
  'image_sha256': '9b742de32dbba2562af0a84343054dd3d8a375f95d147a4621dec4753a89ea30',
  'licence': 'NOT_IN_COPYRIGHT',
  'source': 'https://archive.org/details/formssupplements00stanrich',
  'truth_sha256': '1f9856ef10d68db90683d2fcb65307151baa43f6d40755dff74f2f499a94591c'},
 {'id': 'price4',
  'kind': 'price list',
  'image_url': 'https://archive.org/download/CAT31289243/page/n5.jpg',
  'image_sha256': 'b988157dbb9a3eb45b9a0f33bdc018bd4bba57787f890e6ecce0d0bef90467b9',
  'licence': 'Public domain. The BHL considers that this work is no longer under copyright '
             'protection.',
  'source': 'https://archive.org/details/CAT31289243',
  'truth_sha256': '7c77c9ba9fbdd1f2bd61b2758ba196ca92e2ffc208dfbda620aa9cad0067e181'},
 {'id': 'poster',
  'kind': 'poster',
  'image_url': 'https://upload.wikimedia.org/wikipedia/commons/d/d8/%22Prices_charged_in_this_store_will_not_exceed_those_indicated_in_the_most_recent_list_of_Fair_Prices_applicable_to_this_-_NARA_-_512556.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original',
  'image_sha256': 'dcc6953d79a87749024d8b278906042f33e4d01b56d6d546af594a448793b171',
  'licence': 'Public domain',
  'source': 'https://commons.wikimedia.org/wiki/File:%22Prices_charged_in_this_store_will_not_exceed_those_indicated_in_the_most_recent_list_of_Fair_Prices_applicable_to_this_-_NARA_-_512556.jpg',
  'truth_sha256': 'a9d573b62310d5307ef3173154f9dfcd9718d22d4066b9dcc800a94e30902379',
  'oldid': 14931103},
 {'id': 'eb5_300',
  'kind': 'two-column print',
  'image_url': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/8/83/EB1911_-_Volume_05.djvu/page300-1920px-EB1911_-_Volume_05.djvu.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail',
  'image_sha256': 'c55ca55606508d00ebf02bdac40eb39795fd3b45d7f72b1e00a6401921b00edf',
  'licence': 'Public domain',
  'source': 'https://commons.wikimedia.org/wiki/File:EB1911_-_Volume_05.djvu',
  'truth_sha256': '6c3ffd998fe2e5c0c4d15cefd982dc82036e24ad1b050a6a798df207d4141cd0',
  'oldid': 12483976},
 {'id': 'eb5_700',
  'kind': 'two-column print',
  'image_url': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/8/83/EB1911_-_Volume_05.djvu/page700-1920px-EB1911_-_Volume_05.djvu.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail',
  'image_sha256': '6d954e11df55acfa3d0aaf435d12c5bb550dbf2cdf64d27c8238b7b74006de5c',
  'licence': 'Public domain',
  'source': 'https://commons.wikimedia.org/wiki/File:EB1911_-_Volume_05.djvu',
  'truth_sha256': '86d368e0d6f5909b6ccfdaae304d45b789e8c851a297a57c4b672a2488f74c08',
  'oldid': 12164111},
 {'id': 'eb10_300',
  'kind': 'two-column print',
  'image_url': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b1/EB1911_-_Volume_10.djvu/page300-1920px-EB1911_-_Volume_10.djvu.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail',
  'image_sha256': '14394da46cd1d107ab84767b7ca647b61d28d8d92556a54901744cfce3fba6c7',
  'licence': 'Public domain',
  'source': 'https://commons.wikimedia.org/wiki/File:EB1911_-_Volume_10.djvu',
  'truth_sha256': '061a3aa3b69aef98582d5c853aaf6307e9488b468b03e4c30a991b54ecafe3a1',
  'oldid': 15018851},
 {'id': 'eb10_700',
  'kind': 'two-column print',
  'image_url': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b1/EB1911_-_Volume_10.djvu/page700-1920px-EB1911_-_Volume_10.djvu.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail',
  'image_sha256': '9c8595fa30a302702bd7fbbaec59f5eca9b4c7e7b643392c6583e87f6c5e4e7d',
  'licence': 'Public domain',
  'source': 'https://commons.wikimedia.org/wiki/File:EB1911_-_Volume_10.djvu',
  'truth_sha256': '57a7ac1a0709cc8fb84b2944579cb343be7cb2cc7edf368e5f85ba38ab882beb',
  'oldid': 12422798},
 {'id': 'amp60',
  'kind': 'manual',
  'image_url': 'https://archive.org/download/americanprinterm00mack/page/n59.jpg',
  'image_sha256': '5a6d86906cbaf851ec6620ba7bcbc2df255c782c354634bce07b8d4752533533',
  'licence': 'NOT_IN_COPYRIGHT',
  'source': 'https://archive.org/details/americanprinterm00mack',
  'truth_sha256': '868c9eac2420ad5caa8a6414772cce4721be1e5126c88f97ad8b123636ea2866',
  'oldid': 16132937},
 {'id': 'amp61',
  'kind': 'type specimens',
  'image_url': 'https://archive.org/download/americanprinterm00mack/page/n60.jpg',
  'image_sha256': '8bc9fe61b20e385bd59de334e3ad5454b17f823db5b2f5511b5961fedbff55a4',
  'licence': 'NOT_IN_COPYRIGHT',
  'source': 'https://archive.org/details/americanprinterm00mack',
  'truth_sha256': '40cf34a1a7bc232a968efd2dd320b029aa542e697aec1b44db95398e36a5621d',
  'oldid': 16133039},
 {'id': 'amp62',
  'kind': 'type specimens',
  'image_url': 'https://archive.org/download/americanprinterm00mack/page/n61.jpg',
  'image_sha256': '96b3a147640a84cfc9443bc06b520445467534dbb9f94aa3678ae263f5a58e54',
  'licence': 'NOT_IN_COPYRIGHT',
  'source': 'https://archive.org/details/americanprinterm00mack',
  'truth_sha256': '1fc4ad12ac238743be4d41aaa789cdb3e33bfeb7fba913bd2fd77f5316c98e28',
  'oldid': 16133065},
 {'id': 'amp63',
  'kind': 'manual',
  'image_url': 'https://archive.org/download/americanprinterm00mack/page/n62.jpg',
  'image_sha256': '4853c40c3d4506b2a1f094b13fc739468e8632cd566afe1b928fb64cad3c8bfa',
  'licence': 'NOT_IN_COPYRIGHT',
  'source': 'https://archive.org/details/americanprinterm00mack',
  'truth_sha256': '8949f9bd440938d34364c498f38e924005ee76300cdd88a5688ff17d6452c317',
  'oldid': 16130092}]

HAND_TYPED = {'form12': 'Fill work sheet first\n'
           'DELIVER OR SEND THIS RETURN WITH PAYMENT TO COLLECTOR OF INTERNAL REVENUE ON OR BEFORE '
           'MARCH 15, 1919\n'
           'DETACH AND KEEP WORK SHEET AND INSTRUCTION SHEET\n'
           'Form 1040A.—UNITED STATES INTERNAL REVENUE SERVICE\n'
           'INDIVIDUAL INCOME TAX RETURN\n'
           'FOR NET INCOMES OF NOT MORE THAN $5,000\n'
           'For Calendar Year 1918\n'
           'IF NAME AND ADDRESS PRINTED BELOW ARE INCORRECT PLEASE MAKE CORRECTIONS\n'
           'JOHN DOE\n'
           '5 West 10th Street\n'
           'New York City.\n'
           'Do not write in this space\n'
           'FIRST PAYMENT\n'
           '$\n'
           "(Cashier's Stamp.)\n"
           'CASH CHECK M. O.\n'
           'Examined by\n'
           '1. Did you make a return for 1917? Yes\n'
           '2. If so, what address did you give on that return? 5 West 10th Street, New York City\n'
           "3. To what collector's office was it sent? 2nd District, New York.\n"
           '(Give district or city and State.)\n'
           '4. Give number, if any, assigned to you for 1917 if it does not appear in address at '
           'head of return 651397\n'
           '5. Were you in 1918 married and living with wife (or husband)? Yes\n'
           '6. If not, were you the head of a family as defined in instructions under "Personal '
           'Exemption?" ---\n'
           '7. How many dependent persons under 18 (or mentally or physically defective) received '
           'their chief support from you during 1918? One\n'
           '8. If you claim any additional exemption on account of dependent persons other than '
           'your children, what was their relationship to you? ------\n'
           '9. Write "R" if this return shows income received, or "A" if it shows income accrued '
           '"R"\n'
           '10. Did your wife (or husband) or minor child make a separate return? No\n'
           '(If so, give name and address thereon.)\n'
           '11. Did you or your wife (or husband) or dependent minor children receive any interest '
           'on U. S. Liberty Bonds, or any salary not reported elsewhere in this return or in a '
           'separate return? No\n'
           '(If so, give sources and amounts.)\n'
           '12. Enter name and address of each organization to which you made contributions '
           'claimed as deductions, and amount paid to each.\n'
           'Red Cross New York City $25.00\n'
           'Y. M. C. A. " " " 25.00\n'
           '13. Enter in this table details concerning repairs, wear and tear, and property '
           'losses, claimed as deductions in Schedules A, E, and I on page 2 of return (see '
           'instructions):\n'
           '1. Refer to "A," "E," or "I."\n'
           '2. Kind of property. (If buildings, state also material of which constructed.)\n'
           '3. Year acquired.\n'
           '4. Cost of property (or market value March 1, 1913).\n'
           '5. Repairs not offset by claims for wear and tear or losses.\n'
           'Wear and tear (depreciation) and depletion charged off—\n'
           '6. Rate.\n'
           '7. Amount previous years.\n'
           '8. Amount this year.\n'
           'Losses not compensated for by insurance.\n'
           '9. Cause of loss.\n'
           '10. Amount of loss.\n'
           '"A" Fur. & Fix. 1916 $ 8000 -- $ ---- 10% $ 800 $ 800 $\n'
           '"E" Stable 1916 10000 -- 200 10% 1000 1000\n'
           'CALCULATION OF TAX\n'
           'Do not write here\n'
           'M. Net income shown on page 2, Item J $ 3415\n'
           'N. Less personal exemption (see Instruction VI) 2200\n'
           'O. Balance (income taxable at 6%) $ 1215\n'
           'NOTE.—If the amount on line O exceeds $4,000, the excess is taxable at 12%, and your '
           'return should be made on Form 1040.\n'
           'Do not write here.\n'
           'P. Tax due (6% on amount of Item O) $ 72 90\n'
           'Q. Less normal tax of 2% on Item F 2 00\n'
           'R. Balance of tax due $ 70 90\n'
           'S. Amount of tax paid on submission of return $ 17 73\n'
           'AFFIDAVIT\n'
           'I swear (or affirm) that this return, to the best of my knowledge and belief, is a '
           'true and complete statement of all taxable gains, profits, and income received by or '
           'accrued to me (or the person for whom this return is made) during the year 1918, and '
           'that all deductions entered or claimed herein are allowable under the law.\n'
           '(If return is made by agent, the reason therefor must be stated on this line.)\n'
           'Sworn to and subscribed before me this day of , 1919. (Signed) John Doe\n'
           '(Signature of individual or agent.)\n'
           '(Signature of officer administering oath.) (Title.) (Address of individual or agent.)\n'
           'DETACH RETURN HERE AND SEND IT TO COLLECTOR OF INTERNAL REVENUE AT CUSTOMHOUSE, NEW '
           'YORK, N. Y.\n',
 'form16': 'CROP PRODUCTS SOLD AND ON HAND\n'
           'Kind\n'
           'On hand at beginning of year\n'
           'Quantity Inventory value\n'
           'Sold during year\n'
           'Quantity Sale price Per unit Total\n'
           'On hand at end of year\n'
           'Quantity Inventory value\n'
           '$ $ $\n'
           'N O N E\n'
           'Totals $ $ $\n'
           '(8) (9) (10)\n'
           'LIVE STOCK PRODUCTS SOLD AND ON HAND\n'
           'Milk $ $ $\n'
           'Butter 10 lbs. 4 00 500 lb 45¢ 225 00 20 lb 80 00\n'
           'Cream\n'
           'Cheese\n'
           'Eggs 5 doz 2 00 1080 doz 40¢ 432 00 10doz 3 50\n'
           'Wool 100 lbs. 20 00 250 lb. 25¢ 62 50 -\n'
           'Hides\n'
           'Honey\n'
           'Totals $ 26 00 $ 719 50 $ 83 50\n'
           '(11) (12) (13)\n'
           'MISCELLANEOUS RECEIPTS\n'
           'Description Amount\n'
           'Machine work, hire of teams, etc. $\n'
           'Baling Hay 50 00\n'
           'Hauling 1450 00\n'
           'Road work 350 00\n'
           'Total $1850 00\n'
           '(14)\n'
           'INVENTORY OF SUPPLIES\n'
           'Kind\n'
           'Inventory Value\n'
           'At beginning of year At end of year\n'
           'Purchased feeds $ 25 00 $ 40 00\n'
           'Seeds 10 00 -\n'
           'Fertilizers 200 00 100 00\n'
           'Spray materials - 10 00\n'
           'Totals $ 235 00 $ 150 00\n'
           '(15) (16)\n'
           'EXPENSES\n'
           'Items Amount\n'
           'Hired labor (regular) $ 600 00\n'
           'Hired labor (extra) 200 00\n'
           'Board of hired labor 200 00\n'
           'Cost of purchased materials furnished labor 160 00\n'
           'Feed, hay, straw, etc\n'
           'Feed, grain, concentrates 200 00\n'
           'Feed grinding\n'
           'Silo filling\n'
           'Corn shredding\n'
           'Cotton ginning\n'
           'Milk hauling\n'
           'Ice 20 00\n'
           'Horseshoeing 50 00\n'
           'Breeding fees\n'
           'Veterinary fees 20 00\n'
           'Seed, plants, etc 10 00\n'
           'Fertilizers, manure 100 00\n'
           'Spray materials\n'
           'Twine 10 00\n'
           'Threshing 50 00\n'
           'Baling\n'
           'Machine work hired\n'
           'Fuel and oil for farm work\n'
           'Barrels, bags, crates 10 00\n'
           'Farm insurance 25 00\n'
           'Taxes 75 00\n'
           'Water rent\n'
           'Cash rent\n'
           'Interest on farm notes and mortgages 300 00\n'
           'Total $2030 00\n'
           '(17)\n',
 'price4': 'APPLES—continued\n'
           '7-8 11-16 9-16 7-16 5-16 2-3\n'
           'Red Canada .... 90 20 15 40 ....\n'
           '† Romanstem 10 1060 800 420 240 160\n'
           'Red June 70 160 .... 90 300 200\n'
           'Rome Beauty 30 200 60 .... .... ....\n'
           '† Salome 20 300 500 190 400 140\n'
           "† Stayman's Winesap 300 650 120 200 270 ....\n"
           'Sweet Bough .... 200 200 .... 65 40\n'
           'Sweet Russet .... 540 150 .... 120 20\n'
           '†*Tolman Sweet 180 1200 600 300 800 ....\n'
           'Turley .... 430 280 110 250 25\n'
           'Wagener .... .... 15 50 175 70\n'
           '*Wealthy 10 1150 820 430 1400 530\n'
           'Willow Twig .... 90 80 30 75 ....\n'
           '*Windsor Chief .... 120 360 90 260 100\n'
           '† Winesap 100 620 60 330 200 100\n'
           'Winter Paradise Sweet 25 90 10 .... .... ....\n'
           '*Wolf River 15 30 .... .... .... ....\n'
           'W. W. Pearmain .... 18 30 28 20 ....\n'
           '† *Yellow Transparent 360 2360 1250 540 1800 200\n'
           '† York Imperial 200 1680 500 130 200 ....\n'
           'APPLES\n'
           'Per 10 Per 100 Per 1000\n'
           'First Class, 7-8 inch, 5 feet and up $6 00 $50 00 $......\n'
           '" " 11-16 to 1 inch, 4½ feet and up 5 00 45 00 ......\n'
           '" " 9-16 to 11-16, 4 feet and up 4 40 38 00 ......\n'
           '" " 7-16 to 9-16, 3 feet and up 3 50 30 00 270 00\n'
           '" " 5-16 to 7-16, 2 feet and up 2 60 22 00 200 00\n'
           '" " 2 to 3 feet, under 5-16 2 10 17 50 150 00\n'
           '7-8 11-16 9-16 7-16 5-16 2-3\n'
           'Chenango .... 10 .... .... 20 5\n'
           'Cortland 25 300 .... 45 .... 75\n'
           'Duchess 25 50 50 300 900 500\n'
           '*Duchess Red .... 70 .... 150 400 450\n'
           'Early McIntosh .... .... 80 230 100 25\n'
           'Florence .... 90 30 50 150 75\n'
           'Haralson .... 50 160 460 900 200\n'
           'Jonathan Dark Red .... 140 80 45 120 25\n'
           '*McIntosh .... 170 .... 400 100 500\n'
           'McIntosh Dark Red .... .... 10 60 60 10\n'
           'R. I. Greening 10 .... .... .... 40 15\n'
           'Rome Beauty Red .... 40 70 15 .... 10\n'
           'Sharon .... .... 65 85 75 ....\n'
           'St. Lawrence .... 50 15 15 65 ....\n'
           'Tetofsky .... .... 45 45 90 20\n'
           '*Whitney Crab 10 220 50 100 450 470\n'
           '† Write for special prices on quantity lots.\n'
           '* Varieties starred * can be furnished in budded trees.\n'}


# --- round 3's frozen Wikisource cleaning rules (clean_ws.py) --------------

_DROP = {"dhr", "nop", "clear", "rule", "rh", "smallrefs", "eb1911 fine print/s",
         "eb1911 fine print/e", "eb1911 footer initials", "gap", "em", "dotted tab",
         "reflist", "pagequality", "hr"}


def _split_args(body):
    args, depth, cur, i = [], 0, "", 0
    while i < len(body):
        if body.startswith("{{", i) or body.startswith("[[", i):
            depth += 1
            cur += body[i:i + 2]
            i += 2
            continue
        if (body.startswith("}}", i) or body.startswith("]]", i)) and depth:
            depth -= 1
            cur += body[i:i + 2]
            i += 2
            continue
        if body[i] == "|" and depth == 0:
            args.append(cur)
            cur = ""
            i += 1
            continue
        cur += body[i]
        i += 1
    args.append(cur)
    return args


def _render_template(body):
    args = _split_args(body)
    name = args[0].strip().lower()
    pos = [a for a in args[1:] if not re.match(r"^\s*[\w -]+\s*=", a)]
    if name in _DROP or name.startswith("rule"):
        return ""
    if name in ("eb1911 article link", "dnb lkpl"):
        return pos[0] if pos else ""
    if name == "eb1911 tfrac":
        return "/".join(p.strip() for p in pos[-2:]) if len(pos) >= 2 else (pos[0] if pos else "")
    return pos[-1] if pos else ""


def _templates(w):
    out, i = "", 0
    while i < len(w):
        if w.startswith("{{", i):
            depth, j = 0, i
            while j < len(w):
                if w.startswith("{{", j):
                    depth += 1
                    j += 2
                    continue
                if w.startswith("}}", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                    continue
                j += 1
            inner = w[i + 2:j - 2]
            args = _split_args(inner)
            inner = "|".join([args[0]] + [_templates(a) for a in args[1:]])
            out += _templates(_render_template(inner))
            i = j
        else:
            out += w[i]
            i += 1
    return out


def clean_wikitext(w):
    w = re.sub(r"<noinclude>.*?</noinclude>", "", w, flags=re.S)
    w = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", w, flags=re.S)
    w = re.sub(r"<!--.*?-->", "", w, flags=re.S)
    w = _templates(w)
    w = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", w)
    w = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", w)
    w = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", w)
    w = re.sub(r"'''?", "", w)
    w = re.sub(r"<br\s*/?>", "\n", w)
    w = re.sub(r"<[^>]+>", " ", w)
    w = html.unescape(w).replace("\xa0", " ")
    return "\n".join(ln.strip() for ln in w.splitlines() if ln.strip())


# --- round 1's scorer with round 3's dot rule ------------------------------

_DOTS = re.compile(r"(?:\.\s?){3,}")


def _lev(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _words(s):
    out = []
    for w in s.split():
        w = w.strip(".,;:!?\"'()[]{}").lower()
        if w:
            out.append(w)
    return out


def score(truth, got):
    truth, got = _DOTS.sub(" ", truth), _DOTS.sub(" ", got)
    t, g = re.sub(r"\s+", " ", truth).strip(), re.sub(r"\s+", " ", got).strip()
    tw, gw = Counter(_words(truth)), Counter(_words(got))
    hit = sum((tw & gw).values())
    return {"cer": round(_lev(t, g) / max(1, len(t)), 4),
            "word_recall": round(hit / max(1, sum(tw.values())), 4)}


# --- fetching ---------------------------------------------------------------

def _get(url, timeout=120):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def truth_for(page):
    if page["id"] in HAND_TYPED:
        text = HAND_TYPED[page["id"]]
    else:
        q = urllib.parse.urlencode({"action": "query", "prop": "revisions", "revids": page["oldid"],
                                    "rvprop": "content", "rvslots": "main",
                                    "format": "json", "formatversion": "2"})
        data = json.loads(_get(f"{WS_API}?{q}", timeout=60))
        text = clean_wikitext(data["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"])
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != page["truth_sha256"]:
        raise SystemExit(f"{page['id']}: ground truth hash mismatch; refusing to score")
    return text


def main() -> int:
    from PIL import Image

    from jdocmunch_mcp.parser import ocr

    engine, why = ocr.detect_engine()
    if engine is None:
        print(f"No OCR engine: {why}")
        return 2
    print(f"Engine: {engine.identity}\n")
    rows = []
    with tempfile.TemporaryDirectory(prefix="jdoc-ocr-regression-") as tmp:
        for page in MANIFEST:
            data = _get(page["image_url"])
            if hashlib.sha256(data).hexdigest() != page["image_sha256"]:
                raise SystemExit(f"{page['id']}: image hash mismatch; refusing to score")
            path = Path(tmp) / f"{page['id']}.img"
            path.write_bytes(data)
            img = Image.open(path).convert("RGB")
            t0 = time.perf_counter()
            got = engine.read(img).text
            secs = time.perf_counter() - t0
            img.close()
            path.unlink()
            s = score(truth_for(page), got)
            rows.append({"id": page["id"], "kind": page["kind"], **s, "seconds": round(secs, 2)})
            print(f"{page['id']:10} {page['kind']:16} recall {s['word_recall']:.4f}  "
                  f"CER {s['cer']:.3f}  {secs:5.1f} s")
    prose = [r for r in rows if r["id"] in PROSE]
    recall = statistics.mean(r["word_recall"] for r in prose)
    cer = statistics.mean(r["cer"] for r in prose)
    base = SHIPPED_BASELINE.get(engine.name)
    print(f"\nProse (6 pages): mean word recall {recall:.4f}, mean CER {cer:.3f}. "
          f"Bar: >= {BAR_RECALL}, <= {BAR_CER}."
          + (f" Shipped baseline for {engine.name}: {base[0]} / {base[1]}." if base else ""))
    ok = recall >= BAR_RECALL and cer <= BAR_CER
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
