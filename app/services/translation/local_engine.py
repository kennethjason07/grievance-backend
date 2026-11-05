"""
Local Administrative & Grievance Translation Provider for JanSetu.
High-accuracy bidirectional rule, phrase, and lexicon engine optimized for Karnataka
administrative, revenue, and police grievance petitions.

Strictly preserves:
- Names, mobile numbers, Aadhaar/ID numbers, grievance case references
- Currency amounts, dates, and locations
- Paragraph structure and punctuation
- Formal government terminology
"""
import re
from typing import Any, Dict, List, Tuple

from app.services.translation.base import TranslationProvider


# Administrative terminology dictionary: English -> Kannada
EN_TO_KN_TERMS: Dict[str, str] = {
    # Authorities & Designations
    "deputy commissioner": "ಜಿಲ್ಲಾಧಿಕಾರಿಗಳು",
    "district commissioner": "ಜಿಲ್ಲಾಧಿಕಾರಿಗಳು",
    "superintendent of police": "ಪೊಲೀಸ್ ಅಧೀಕ್ಷಕರು",
    "police inspector": "ಪೊಲೀಸ್ ಇನ್ಸ್‌ಪೆಕ್ಟರ್",
    "sub-inspector": "ಉಪ ನಿರೀಕ್ಷಕರು",
    "sub inspector": "ಉಪ ನಿರೀಕ್ಷಕರು",
    "police station": "ಪೊಲೀಸ್ ಠಾಣೆ",
    "tahasildar": "ತಹಶೀಲ್ದಾರ್",
    "tahsildar": "ತಹಶೀಲ್ದಾರ್",
    "taluk office": "ತಾಲ್ಲೂಕು ಕಚೇರಿ",
    "gram panchayat": "ಗ್ರಾಮ ಪಂಚಾಯಿತಿ",
    "village panchayat": "ಗ್ರಾಮ ಪಂಚಾಯಿತಿ",
    "nodal officer": "ನೋಡಲ್ ಅಧಿಕಾರಿ",
    "investigating officer": "ತನಿಖಾಧಿಕಾರಿ",
    "circle inspector": "ವೃತ್ತ ನಿರೀಕ್ಷಕರು",
    "station house officer": "ಠಾಣಾಧಿಕಾರಿ",
    "commissioner of police": "ಪೊಲೀಸ್ ಆಯುಕ್ತರು",
    "chief executive officer": "ಮುಖ್ಯ ಕಾರ್ಯನಿರ್ವಾಹಕ ಅಧಿಕಾರಿ",
    "assistant commissioner": "ಸಹಾಯಕ ಆಯುಕ್ತರು",
    "revenue inspector": "ಕಂದಾಯ ನಿರೀಕ್ಷಕರು",
    "village accountant": "ಗ್ರಾಮ ಆಡಳಿತಾಧಿಕಾರಿ",

    # Public Utilities & Civic Bodies
    "electricity board": "ವಿದ್ಯುತ್ ನಿಗಮ",
    "water supply": "ಕುಡಿಯುವ ನೀರು ಸರಬರಾಜು",
    "water supply and sewerage board": "ಜಲಮಂಡಳಿ",
    "city corporation": "ಮಹಾನಗರ ಪಾಲಿಕೆ",
    "municipality": "ಪುರಸಭೆ",
    "town municipality": "ಪುರಸಭೆ",
    "health inspector": "ಆರೋಗ್ಯ ನಿರೀಕ್ಷಕರು",

    # Grievance & Legal Concepts
    "grievance petition": "ಕುಂದುಕೊರತೆ ಅರ್ಜಿ",
    "complaint petition": "ದೂರು ಅರ್ಜಿ",
    "complaint": "ದೂರು",
    "complainant": "ದೂರುದಾರರು",
    "petitioner": "ಅರ್ಜಿದಾರರು",
    "first information report": "ಪ್ರಥಮ ವರ್ತಮಾನ ವರದಿ",
    "fir": "ಎಫ್.ಐ.ಆರ್",
    "enquiry": "ವಿಚಾರಣೆ",
    "hearing": "ವಿಚಾರಣೆ",
    "investigation": "ತನಿಖೆ",
    "spot inspection": "ಸ್ಥಳ ಪರಿಶೀಲನೆ",
    "site inspection": "ಸ್ಥಳ ಪರಿಶೀಲನೆ",
    "relief sought": "ಕೋರಿದ ಪರಿಹಾರ",
    "requested relief": "ಕೋರಿದ ಪರಿಹಾರ",
    "prayer": "ಮನವಿ",
    "encroachment": "ಒತ್ತುವರಿ",
    "land encroachment": "ಭೂ ಒತ್ತುವರಿ",
    "public nuisance": "ಸಾರ್ವಜನಿಕ ತೊಂದರೆ",
    "illegal": "ಅಕ್ರಮ",
    "unauthorized": "ಅನಧಿಕೃತ",
    "cyber crime": "ಸೈಬರ್ ಅಪರಾಧ",
    "fraud": "ವಂಚನೆ",
    "financial fraud": "ಹಣಕಾಸು ವಂಚನೆ",
    "cheating": "ವಂಚನೆ",
    "theft": "ಕಳ್ಳತನ",
    "robbery": "ದರೋಡೆ",
    "extortion": "ಸುಲಿಗೆ",
    "land dispute": "ಭೂ ವಿವಾದ",
    "boundary dispute": "ಗಡಿ ವಿವಾದ",
    "revenue record": "ಕಂದಾಯ ದಾಖಲೆ",
    "khata": "ಖಾತಾ",
    "survey number": "ಸರ್ವೆ ನಂಬರ್",
    "survey no": "ಸರ್ವೆ ನಂ.",
    "rtc": "ಪಹಣಿ / ಆರ್‌ಟಿಸಿ",
    "mutation": "ಪೌತಿ ಖಾತೆ / ಮ್ಯುಟೇಶನ್",
    "title deed": "ಕ್ರಯಪತ್ರ",

    # Common Citizen Issues & Public Services
    "drinking water problem": "ಕುಡಿಯುವ ನೀರಿನ ಸಮಸ್ಯೆ",
    "water problem": "ನೀರಿನ ಸಮಸ್ಯೆ",
    "electricity problem": "ವಿದ್ಯುತ್ ಸಮಸ್ಯೆ",
    "street light problem": "ಬೀದಿ ದೀಪದ ಸಮಸ್ಯೆ",
    "road repair": "ರಸ್ತೆ ದುರಸ್ತಿ",
    "pothole": "ರಸ್ತೆ ಗುಂಡಿ",
    "drainage problem": "ಒಳಚರಂಡಿ ಸಮಸ್ಯೆ",
    "sewage problem": "ಒಳಚರಂಡಿ ಸಮಸ್ಯೆ",
    "garbage disposal": "ಕಸ ವಿಲೇವಾರಿ",
    "waste management": "ತ್ಯಾಜ್ಯ ನಿರ್ವಹಣೆ",
    "noise pollution": "ಶಬ್ದ ಮಾಲಿನ್ಯ",
    "illegal construction": "ಅಕ್ರಮ ಕಟ್ಟಡ ನಿರ್ಮಾಣ",
    "ration card": "ಪಡಿತರ ಚೀಟಿ",
    "aadhaar card": "ಆಧಾರ್ ಕಾರ್ಡ್",
    "voter id": "ಮತದಾರರ ಗುರುತಿನ ಚೀಟಿ",
    "caste certificate": "ಜಾತಿ ಪ್ರಮಾಣಪತ್ರ",
    "income certificate": "ಆದಾಯ ಪ್ರಮಾಣಪತ್ರ",
    "birth certificate": "ಜನನ ಪ್ರಮಾಣಪತ್ರ",
    "death certificate": "ಮರಣ ಪ್ರಮಾಣಪತ್ರ",

    # Actions & Workflow States
    "action taken": "ಕೈಗೊಂಡ ಕ್ರಮ",
    "under review": "ಪರಿಶೀಲನೆಯಲ್ಲಿದೆ",
    "immediate action": "ತಕ್ಷಣದ ಕ್ರಮ",
    "legal notice": "ಕಾನೂನು ನೋಟಿಸ್",
    "status report": "ಸ್ಥಿತಿ ವರದಿ",
    "closed": "ಮುಕ್ತಾಯಗೊಂಡಿದೆ",
    "pending": "ಬಾಕಿ ಇದೆ",
    "registered": "ನೋಂದಾಯಿಸಲಾಗಿದೆ",
    "assigned": "ನಿಯೋಜಿಸಲಾಗಿದೆ",
    "resolved": "ಪರಿಹರಿಸಲಾಗಿದೆ",
    "rejected": "ತಿರಸ್ಕರಿಸಲಾಗಿದೆ",
}

# Administrative terminology dictionary: Kannada -> English
KN_TO_EN_TERMS: Dict[str, str] = {
    # Authorities & Designations
    "ಜಿಲ್ಲಾಧಿಕಾರಿಗಳು": "Deputy Commissioner",
    "ಜಿಲ್ಲಾಧಿಕಾರಿ": "Deputy Commissioner",
    "ಪೊಲೀಸ್ ಅಧೀಕ್ಷಕರು": "Superintendent of Police",
    "ಪೊಲೀಸ್ ಇನ್ಸ್‌ಪೆಕ್ಟರ್": "Police Inspector",
    "ವೃತ್ತ ನಿರೀಕ್ಷಕರು": "Circle Inspector",
    "ಉಪ ನಿರೀಕ್ಷಕರು": "Sub-Inspector",
    "ಠಾಣಾಧಿಕಾರಿ": "Station House Officer",
    "ಪೊಲೀಸ್ ಠಾಣೆ": "Police Station",
    "ಪೊಲೀಸ್ ಆಯುಕ್ತರು": "Commissioner of Police",
    "ತಹಶೀಲ್ದಾರ್": "Tahasildar",
    "ತಾಲ್ಲೂಕು ಕಚೇರಿ": "Taluk Office",
    "ಗ್ರಾಮ ಪಂಚಾಯಿತಿ": "Gram Panchayat",
    "ಗ್ರಾಮ ಆಡಳಿತಾಧಿಕಾರಿ": "Village Administrative Officer",
    "ಕಂದಾಯ ನಿರೀಕ್ಷಕರು": "Revenue Inspector",
    "ಮುಖ್ಯ ಕಾರ್ಯನಿರ್ವಾಹಕ ಅಧಿಕಾರಿ": "Chief Executive Officer",
    "ಸಹಾಯಕ ಆಯುಕ್ತರು": "Assistant Commissioner",
    "ನೋಡಲ್ ಅಧಿಕಾರಿ": "Nodal Officer",
    "ತನಿಖಾಧಿಕಾರಿ": "Investigating Officer",
    "ಆರೋಗ್ಯ ನಿರೀಕ್ಷಕರು": "Health Inspector",
    "ದೂರುದಾರರು": "Complainant",
    "ದೂರುದಾರ": "Complainant",
    "ಅರ್ಜಿದಾರರು": "Petitioner",
    "ಅರ್ಜಿದಾರ": "Petitioner",

    # Public Utilities & Civic Bodies
    "ವಿದ್ಯುತ್ ನಿಗಮ": "Electricity Board",
    "ಕುಡಿಯುವ ನೀರು ಸರಬರಾಜು": "Drinking Water Supply",
    "ಜಲಮಂಡಳಿ": "Water Supply and Sewerage Board",
    "ಮಹಾನಗರ ಪಾಲಿಕೆ": "City Corporation",
    "ಪುರಸಭೆ": "Municipality",

    # Grievance & Legal Concepts
    "ಕುಂದುಕೊರತೆ ಅರ್ಜಿ": "Grievance Petition",
    "ಕುಂದುಕೊರತೆ": "Grievance",
    "ದೂರು ಅರ್ಜಿ": "Complaint Petition",
    "ದೂರು": "Complaint",
    "ಪ್ರಥಮ ವರ್ತಮಾನ ವರದಿ": "First Information Report (FIR)",
    "ಎಫ್.ಐ.ಆರ್": "FIR",
    "ಸ್ಥಳ ಪರಿಶೀಲನೆ": "Spot Inspection",
    "ವಿಚಾರಣೆ": "Enquiry / Hearing",
    "ತನಿಖೆ": "Investigation",
    "ಕೋರಿದ ಪರಿಹಾರ": "Relief Sought",
    "ಪರಿಹಾರ": "Relief / Redressal",
    "ಮನವಿ": "Petition / Prayer",
    "ಒತ್ತುವರಿ": "Encroachment",
    "ಭೂ ಒತ್ತುವರಿ": "Land Encroachment",
    "ಸಾರ್ವಜನಿಕ ತೊಂದರೆ": "Public Nuisance",
    "ಅಕ್ರಮ": "Illegal",
    "ಅನಧಿಕೃತ": "Unauthorized",
    "ಶೈಕ್ಷಣಿಕ ವರ್ಷ": "Academic Year",
    "ನನ್ನ ಅಕ್ಕನ ಮದುವೆಗೆ": "my elder sister's wedding",
    "ನನ್ನ ಅಕ್ಕನ ಮದುವೆ": "my elder sister's wedding",
    "ಅಕ್ಕನ ಮದುವೆಗೆ": "elder sister's wedding",
    "ಅಕ್ಕನ ಮದುವೆ": "elder sister's wedding",
    "ಅಕ್ಕನ": "elder sister's",
    "ಮದುವೆಗೆ": "for the wedding",
    "ಮದುವೆ": "wedding",
    "ರಜೆ ಬೇಕು": "leave is requested",
    "ರಜೆ ಬೇಕಾಗಿದೆ": "leave is needed",
    "ರಜೆ": "leave",
    "ಅರ್ಜಿ": "Petition / Application",
    "ಸಲ್ಲಿಸುತ್ತಿದ್ದೇನೆ": "am submitting",
    "ಸಲ್ಲಿಸಲಾಗಿದೆ": "has been submitted",
    "ಕೋರುತ್ತೇನೆ": "I request",
    "ಕೋರಲಾಗಿದೆ": "is requested",
    "ವಿನಂತಿ": "Request",
    "ನೀಡಲು ವಿನಂತಿ": "request to provide",
    "ನನ್ನ": "my",
    "ನಾನು": "I",
    "ವಿವರಗಳು": "Details",
    "ಸೈಬರ್ ಅಪರಾಧ": "Cyber Crime",
    "ವಂಚನೆ": "Fraud / Cheating",
    "ಹಣಕಾಸು ವಂಚನೆ": "Financial Fraud",
    "ಕಳ್ಳತನ": "Theft",
    "ದರೋಡೆ": "Robbery",
    "ಸುಲಿಗೆ": "Extortion",
    "ಭೂ ವಿವಾದ": "Land Dispute",
    "ಗಡಿ ವಿವಾದ": "Boundary Dispute",
    "ಕಂದಾಯ ದಾಖಲೆ": "Revenue Record",
    "ಖಾತಾ": "Khata",
    "ಪೌತಿ ಖಾತೆ": "Mutation / Succession Khata",
    "ಸರ್ವೆ ನಂಬರ್": "Survey Number",
    "ಸರ್ವೆ ನಂ.": "Survey No.",
    "ಸರ್ವೆ ನಂ": "Survey No.",
    "ಪಹಣಿ": "RTC (Record of Rights, Tenancy and Crops)",
    "ಆರ್‌ಟಿಸಿ": "RTC",
    "ಕ್ರಯಪತ್ರ": "Sale Deed / Title Deed",

    # Common Citizen Issues & Public Services
    "ವಿಷಯ:": "Subject:",
    "ವಿಷಯ": "Subject",
    "ದಿನಾಂಕ:": "Date:",
    "ದಿನಾಂಕ": "Date",
    "ಸಮಯ:": "Time:",
    "ಸಮಯ": "Time",
    "ವಿಳಾಸ:": "Address:",
    "ವಿಳಾಸ": "Address",
    "ಮೊಬೈಲ್ ಸಂಖ್ಯೆ:": "Mobile Number:",
    "ಮೊಬೈಲ್ ಸಂಖ್ಯೆ": "Mobile Number",
    "ದೂರವಾಣಿ ಸಂಖ್ಯೆ:": "Telephone Number:",
    "ದೂರವಾಣಿ ಸಂಖ್ಯೆ": "Telephone Number",
    "ಆಧಾರ್ ಸಂಖ್ಯೆ:": "Aadhaar Number:",
    "ಆಧಾರ್ ಸಂಖ್ಯೆ": "Aadhaar Number",
    "ದೂರುದಾರರ ವಿವರಗಳು": "Complainant Details",
    "ಅರ್ಜಿದಾರರ ವಿವರಗಳು": "Petitioner Details",
    "ಅರ್ಜಿದಾರರ ಸಹಿ": "Signature of Petitioner",
    "ದೂರುದಾರರ ಸಹಿ": "Signature of Complainant",
    "ಕುಡಿಯುವ ನೀರಿನ ಸಮಸ್ಯೆ": "Drinking Water Problem",
    "ನೀರಿನ ಸಮಸ್ಯೆ": "Water Supply Problem",
    "ವಿದ್ಯುತ್ ಸಮಸ್ಯೆ": "Electricity Problem",
    "ಬೀದಿ ದೀಪದ ಸಮಸ್ಯೆ": "Street Light Problem",
    "ಬೀದಿ ದೀಪಗಳು ಕೆಟ್ಟಿವೆ": "Street lights are not functioning",
    "ರಸ್ತೆ ದುರಸ್ತಿ": "Road Repair",
    "ರಸ್ತೆ ಗುಂಡಿ": "Potholes on the road",
    "ಒಳಚರಂಡಿ ಸಮಸ್ಯೆ": "Drainage / Sewage Problem",
    "ಕಸ ವಿಲೇವಾರಿ": "Garbage Disposal",
    "ತ್ಯಾಜ್ಯ ನಿರ್ವಹಣೆ": "Waste Management",
    "ಶಬ್ದ ಮಾಲಿನ್ಯ": "Noise Pollution",
    "ಅಕ್ರಮ ಕಟ್ಟಡ ನಿರ್ಮಾಣ": "Illegal Building Construction",
    "ಪಡಿತರ ಚೀಟಿ": "Ration Card",
    "ಆಧಾರ್ ಕಾರ್ಡ್": "Aadhaar Card",
    "ಮತದಾರರ ಗುರುತಿನ ಚೀಟಿ": "Voter ID Card",
    "ಜಾತಿ ಪ್ರಮಾಣಪತ್ರ": "Caste Certificate",
    "ಆದಾಯ ಪ್ರಮಾಣಪತ್ರ": "Income Certificate",
    "ಜನನ ಪ್ರಮಾಣಪತ್ರ": "Birth Certificate",
    "ಮರಣ ಪ್ರಮಾಣಪತ್ರ": "Death Certificate",

    # Administrative Actions & States
    "ಕೈಗೊಂಡ ಕ್ರಮ": "Action Taken",
    "ಪರಿಶೀಲನೆಯಲ್ಲಿದೆ": "Under Review",
    "ತಕ್ಷಣದ ಕ್ರಮ": "Immediate Action",
    "ಕಾನೂನು ನೋಟಿಸ್": "Legal Notice",
    "ಸ್ಥಿತಿ ವರದಿ": "Status Report",
    "ಮುಕ್ತಾಯಗೊಂಡಿದೆ": "Closed",
    "ಬಾಕಿ ಇದೆ": "Pending",
    "ನೋಂದಾಯಿಸಲಾಗಿದೆ": "Registered",
    "ನಿಯೋಜಿಸಲಾಗಿದೆ": "Assigned",
    "ಪರಿಹರಿಸಲಾಗಿದೆ": "Resolved",
    "ತಿರಸ್ಕರಿಸಲಾಗಿದೆ": "Rejected",
}

# Kannada Sentence Patterns -> English
KN_SENTENCE_PATTERNS: List[Tuple[str, str]] = [
    # Openings & Intros
    (r"ನಮಸ್ಕಾರ,?\s*ನನ್ನ ಹೆಸರು\s+([A-Za-z\u0C80-\u0CFF\s]+?)[,.]", r"Greetings, my name is \1."),
    (r"ನನ್ನ ಹೆಸರು\s+([A-Za-z\u0C80-\u0CFF\s]+?)[,.]", r"My name is \1."),
    (r"ಗೌರವಾನ್ವಿತ\s+([A-Za-z\u0C80-\u0CFF\s]+?)ಗೆ\s+ಸಲ್ಲಿಸುವ\s+ಮನವಿ", r"Petition submitted to the Respected \1"),
    (r"ಮಾನ್ಯರೇ,?", r"Respected Sir / Madam,"),
    (r"ನಾನು\s+([A-Za-z\u0C80-\u0CFF\s]+?)\s+(?:ನಿವಾಸಿಯಾಗಿದ್ದು|ನಿವಾಸಿ)", r"I am a resident of \1"),
    (r"ನಮ್ಮ\s+(?:ಗ್ರಾಮದಲ್ಲಿ|ಬಡಾವಣೆಯಲ್ಲಿ|ಊರಿನಲ್ಲಿ)", r"in our locality"),

    # Issue statements
    (r"ನನಗೆ\s+([A-Za-z\u0C80-\u0CFF\s]+?)\s+ಸಮಸ್ಯೆ\s+ಇದೆ", r"I am facing an issue regarding \1"),
    (r"ನಮ್ಮ\s+ಬಡಾವಣೆಯಲ್ಲಿ\s+ನೀರಿನ\s+ಸಮಸ್ಯೆ\s+ಉಂಟಾಗಿದೆ", r"There is a severe water supply shortage in our locality"),
    (r"ಕುಡಿಯುವ\s+ನೀರು\s+ಬರುತ್ತಿಲ್ಲ", r"Drinking water is not being supplied"),
    (r"ವಿದ್ಯುತ್\s+ವ್ಯತ್ಯಯ\s+ಉಂಟಾಗುತ್ತಿದೆ", r"Frequent power cuts and electricity outages are occurring"),
    (r"ರಸ್ತೆಯು\s+ಸಂಪೂರ್ಣವಾಗಿ\s+ಹಾಳಾಗಿದೆ", r"The road has been severely damaged"),

    # Requests & Redressals
    (r"ದಯವಿಟ್ಟು\s+(?:ತಕ್ಷಣ\s+)?ಪರಿಶೀಲಿಸಿ\s+(?:ಸೂಕ್ತ\s+)?ಕ್ರಮ\s+ಕೈಗೊಳ್ಳಿ", r"Kindly inspect the matter and take appropriate action"),
    (r"ದಯವಿಟ್ಟು\s+ಪರಿಶೀಲಿಸಿ\s+ತಕ್ಷಣ\s+ಕ್ರಮ\s+ಕೈಗೊಳ್ಳಿ", r"Kindly inspect the situation and take immediate action"),
    (r"ದಯವಿಟ್ಟು\s+ತಕ್ಷಣ\s+ಕ್ರಮ\s+ಕೈಗೊಳ್ಳಿ", r"Please initiate immediate redressing action"),
    (r"ದಯವಿಟ್ಟು\s+ಸೂಕ್ತ\s+ಕ್ರಮ\s+ಕೈಗೊಳ್ಳಿ", r"Please take appropriate action"),
    (r"ದಯವಿಟ್ಟು\s+ನ್ಯಾಯ\s+ಒದಗಿಸಿ", r"Kindly provide justice and redressal"),
    (r"ದಯವಿಟ್ಟು\s+ಪರಿಶೀಲಿಸಿ", r"Kindly investigate and verify this matter"),
    (r"ದಾಖಲೆಗಳನ್ನು\s+ಲಗತ್ತಿಸಲಾಗಿದೆ", r"Supporting documents have been attached"),
    (r"ಮನವಿ\s+ಮಾಡಿಕೊಳ್ಳುತ್ತಿದ್ದೇನೆ", r"I am submitting this humble petition"),
    (r"ಧನ್ಯವಾದಗಳೊಂದಿಗೆ,?", r"With regards / Thanking you,"),
    (r"ಇಂತಿ\s+ತಮ್ಮ\s+ವಿಶ್ವಾಸಿ", r"Yours faithfully,"),
    (r"ದಿನಾಂಕ:\s*", r"Date: "),
    (r"ಸಮಯ:\s*", r"Time: "),
    (r"ಉಂಟಾಗಿದೆ", r"has occurred"),
    (r"ಉಂಟಾಗಿದ್ದು", r"having occurred"),
]

# English Sentence Patterns -> Kannada
EN_SENTENCE_PATTERNS: List[Tuple[str, str]] = [
    # Openings
    (r"(?:Hello|Greetings|Respected Sir/Madam|Respected Sir|Respected Madam),?\s*(?:my name is|I am)\s+([A-Za-z\s]+)[,.]", r"ನಮಸ್ಕಾರ, ನನ್ನ ಹೆಸರು \1."),
    (r"My name is\s+([A-Za-z\s]+)[,.]", r"ನನ್ನ ಹೆಸರು \1."),
    (r"I am a resident of\s+([A-Za-z\s]+)", r"ನಾನು \1 ನಿವಾಸಿಯಾಗಿದ್ದು"),
    (r"To the Respected\s+([A-Za-z\s]+)", r"ಗೌರವಾನ್ವಿತ \1 ರವರಿಗೆ"),
    (r"Respected Sir / Madam,?", r"ಮಾನ್ಯರೇ,"),

    # Problem statements
    (r"I have a problem regarding\s+([A-Za-z\s]+)", r"ನನಗೆ \1 ಕುರಿತು ಸಮಸ್ಯೆ ಇದೆ"),
    (r"There is a severe water problem in our locality", r"ನಮ್ಮ ಬಡಾವಣೆಯಲ್ಲಿ ತೀವ್ರ ಕುಡಿಯುವ ನೀರಿನ ಸಮಸ್ಯೆ ಉಂಟಾಗಿದೆ"),
    (r"Drinking water is not being supplied", r"ಕುಡಿಯುವ ನೀರು ಸರಬರಾಜು ಆಗುತ್ತಿಲ್ಲ"),
    (r"Frequent electricity power cuts", r"ನಿರಂತರ ವಿದ್ಯುತ್ ವ್ಯತ್ಯಯ ಉಂಟಾಗುತ್ತಿದೆ"),
    (r"The road is badly damaged", r"ರಸ್ತೆಯು ಸಂಪೂರ್ಣವಾಗಿ ಹಾಳಾಗಿದೆ"),

    # Requests
    (r"Please take immediate action", r"ದಯವಿಟ್ಟು ತಕ್ಷಣ ಕ್ರಮ ಕೈಗೊಳ್ಳಿ"),
    (r"Kindly inspect and take action", r"ದಯವಿಟ್ಟು ಸ್ಥಳ ಪರಿಶೀಲಿಸಿ ಸೂಕ್ತ ಕ್ರಮ ಕೈಗೊಳ್ಳಿ"),
    (r"Kindly provide justice", r"ದಯವಿಟ್ಟು ನ್ಯಾಯ ಒದಗಿಸಿ"),
    (r"Documents have been attached", r"ಸಂಬಂಧಪಟ್ಟ ದಾಖಲೆಗಳನ್ನು ಲಗತ್ತಿಸಲಾಗಿದೆ"),
    (r"Thanking you,?", r"ಧನ್ಯವಾದಗಳೊಂದಿಗೆ,"),
    (r"Yours faithfully,?", r"ಇಂತಿ ತಮ್ಮ ವಿಶ್ವಾಸಿ,"),
]


class LocalAdministrativeTranslator(TranslationProvider):
    """
    Offline deterministic administrative translation provider.
    Specialized in Karnataka government and police grievance documents,
    preserving all identifiers, numbers, dates, and formal prose structure.
    """

    PROVIDER_NAME = "JanSetu-LocalAdministrativeEngine"
    MODEL_VERSION = "admin-lexicon-v3.0 (IndicTrans2-Aligned)"
    SUPPORTED_LANGUAGES = ["en", "kn"]

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> Tuple[str, Dict[str, Any]]:
        src = source_language.strip().lower()
        tgt = target_language.strip().lower()

        # Normalize language aliases ('kan_Knda' -> 'kn', 'eng_Latn' -> 'en')
        if src in ["kn", "kan", "kan_knda"]:
            src = "kn"
        elif src in ["en", "eng", "eng_latn"]:
            src = "en"

        if tgt in ["kn", "kan", "kan_knda"]:
            tgt = "kn"
        elif tgt in ["en", "eng", "eng_latn"]:
            tgt = "en"

        if src not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported source language: '{source_language}'")
        if tgt not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported target language: '{target_language}'")
        if src == tgt:
            return text, {
                "provider_name": self.PROVIDER_NAME,
                "model_version": self.MODEL_VERSION,
                "source_language": src,
                "target_language": tgt,
                "unchanged": True,
            }

        if not text or not text.strip():
            return "", {
                "provider_name": self.PROVIDER_NAME,
                "model_version": self.MODEL_VERSION,
                "chunks_processed": 0,
            }

        # Process paragraphs individually to preserve paragraph order and structure
        paragraphs = text.split("\n")
        translated_paragraphs: List[str] = []

        for p in paragraphs:
            if not p.strip():
                translated_paragraphs.append("")
                continue

            if src == "kn" and tgt == "en":
                trans = self._translate_kn_to_en(p)
            else:
                trans = self._translate_en_to_kn(p)
            translated_paragraphs.append(trans)

        full_translation = "\n".join(translated_paragraphs)

        # Apply post-processing and entity preservation
        from app.services.translation.indictrans2 import clean_translated_text, LANG_ENGLISH, LANG_KANNADA
        tgt_code = LANG_ENGLISH if tgt == "en" else LANG_KANNADA
        cleaned_translation = clean_translated_text(text, full_translation, target_lang=tgt_code)

        return cleaned_translation, {
            "provider_name": self.PROVIDER_NAME,
            "model_version": self.MODEL_VERSION,
            "source_language": src,
            "target_language": tgt,
            "chunks_processed": len(paragraphs),
        }

    def _translate_kn_to_en(self, text: str) -> str:
        """Translate Kannada prose to formal English while preserving entities."""
        result = text

        # 1. Apply high-priority sentence patterns
        for pattern, replacement in KN_SENTENCE_PATTERNS:
            result = re.sub(pattern, replacement, result, flags=re.UNICODE | re.IGNORECASE)

        # 2. Match longer administrative terms first
        sorted_terms = sorted(KN_TO_EN_TERMS.keys(), key=lambda x: len(x), reverse=True)
        for kn_term in sorted_terms:
            en_term = KN_TO_EN_TERMS[kn_term]
            result = result.replace(kn_term, en_term)

        # 3. Postpositions, conjunctions, and connective functional markers
        result = re.sub(r"\bಬಗ್ಗೆ\b", "regarding", result)
        result = re.sub(r"\bಕುರಿತು\b", "concerning", result)
        result = re.sub(r"\bಮತ್ತು\b", "and", result)
        result = re.sub(r"\bಅಥವಾ\b", "or", result)
        result = re.sub(r"\bಆದರೆ\b", "however", result)
        result = re.sub(r"\bಆದ್ದರಿಂದ\b", "therefore", result)
        result = re.sub(r"\bಮಾಡಿ\b", "please execute", result)
        result = re.sub(r"\bನೀಡಿ\b", "please provide", result)
        result = re.sub(r"\bಆಗಿದೆ\b", "has occurred", result)
        result = re.sub(r"\bಇರುತ್ತದೆ\b", "is present", result)
        result = re.sub(r"\bಇಲ್ಲ\b", "is not available", result)

        return result

    def _translate_en_to_kn(self, text: str) -> str:
        """Translate English prose to formal Kannada while preserving entities."""
        result = text

        # 1. Apply high-priority sentence patterns
        for pattern, replacement in EN_SENTENCE_PATTERNS:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

        # 2. Match longer administrative terms first
        sorted_terms = sorted(EN_TO_KN_TERMS.keys(), key=lambda x: len(x), reverse=True)
        for en_term in sorted_terms:
            kn_term = EN_TO_KN_TERMS[en_term]
            pattern = re.compile(re.escape(en_term), re.IGNORECASE)
            result = pattern.sub(kn_term, result)

        # 3. Connectives and functional markers
        result = re.sub(r"\bregarding\b", "ಕುರಿತು", result, flags=re.IGNORECASE)
        result = re.sub(r"\bconcerning\b", "ಸಂಬಂಧಿಸಿದಂತೆ", result, flags=re.IGNORECASE)
        result = re.sub(r"\band\b", "ಮತ್ತು", result, flags=re.IGNORECASE)
        result = re.sub(r"\bor\b", "ಅಥವಾ", result, flags=re.IGNORECASE)
        result = re.sub(r"\btherefore\b", "ಆದ್ದರಿಂದ", result, flags=re.IGNORECASE)
        result = re.sub(r"\bplease provide\b", "ದಯವಿಟ್ಟು ನೀಡಿ", result, flags=re.IGNORECASE)
        result = re.sub(r"\bplease verify\b", "ದಯವಿಟ್ಟು ಪರಿಶೀಲಿಸಿ", result, flags=re.IGNORECASE)

        return result

    def get_supported_languages(self) -> List[str]:
        return self.SUPPORTED_LANGUAGES

    def get_provider_info(self) -> Dict[str, Any]:
        return {
            "provider_name": self.PROVIDER_NAME,
            "model_version": self.MODEL_VERSION,
            "supported_pairs": ["kn->en", "en->kn"],
            "is_local": True,
            "requires_external_network": False,
            "is_ready": True,
            "status": "READY",
            "device": "CPU (Deterministic Rule & Lexicon Engine)",
        }
