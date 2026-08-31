import pymupdf as fitz
import os


def generate_sample_gst_invoice() -> bytes:
    """
    Generates a realistic GST Tax Invoice PDF matching the Demo Subcidys / Reliance Retail format.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # Standard A4 size

    # Background header banner
    page.draw_rect(fitz.Rect(30, 30, 565, 34), color=(0.2, 0.4, 0.8), fill=(0.95, 0.97, 1.0))

    # Seller details (Header)
    page.insert_text(fitz.Point(40, 55), "DEMO SUBCIDYS", fontsize=16, fontname="helv", fontfile=None, color=(0.1, 0.15, 0.3))
    page.insert_text(fitz.Point(40, 72), "GSTIN: 33AAGCB1286Q0ZO  Nagpur Maharashtra  •  PIN: 440001  •  India Mobile: +91-7410852079", fontsize=9, color=(0.3, 0.3, 0.3))

    # Invoice Badge & Number
    page.draw_rect(fitz.Rect(430, 42, 555, 62), color=(0.15, 0.4, 0.9), fill=(0.15, 0.4, 0.9))
    page.insert_text(fitz.Point(450, 56), "TAX INVOICE", fontsize=11, color=(1, 1, 1))

    page.insert_text(fitz.Point(40, 105), "Invoice No.: INV-000002", fontsize=10, fontname="helv", color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(220, 105), "Invoice Date: 22/8/2026", fontsize=10, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(400, 105), "Due Date: 29/8/2026", fontsize=10, color=(0.1, 0.1, 0.1))

    # Separator Line
    page.draw_line(fitz.Point(40, 115), fitz.Point(555, 115), color=(0.85, 0.85, 0.85), width=1)

    # BILL TO & SHIP TO Boxes
    page.draw_rect(fitz.Rect(40, 125, 290, 210), color=(0.85, 0.85, 0.85), fill=(0.98, 0.98, 0.99))
    page.insert_text(fitz.Point(50, 140), "BILL TO", fontsize=10, fontname="helv", color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(50, 156), "Reliance Retail Ltd", fontsize=11, fontname="helv", color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(50, 170), "3rd Floor, Court House, Lokmanya Tilak Marg,", fontsize=9, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(50, 183), "Ghansoli, Maharashtra - 400710 • PIN: 400710", fontsize=9, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(50, 196), "GSTIN: 27AABCR1718E1ZL", fontsize=9, color=(0.2, 0.2, 0.2))

    page.draw_rect(fitz.Rect(305, 125, 555, 210), color=(0.85, 0.85, 0.85), fill=(0.98, 0.98, 0.99))
    page.insert_text(fitz.Point(315, 140), "SHIP TO / CONTACT", fontsize=10, fontname="helv", color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(315, 156), "Reliance Retail Ltd", fontsize=11, fontname="helv", color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(315, 170), "Email: procurement@relianceretail.com", fontsize=9, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(315, 183), "Mobile: +919876543210", fontsize=9, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(315, 196), "Place of Supply: Maharashtra (27)", fontsize=9, color=(0.3, 0.3, 0.3))

    # Line Items Table Header
    page.draw_rect(fitz.Rect(40, 225, 555, 245), color=(0.2, 0.3, 0.6), fill=(0.2, 0.3, 0.6))
    page.insert_text(fitz.Point(45, 239), "S.NO", fontsize=8.5, color=(1, 1, 1))
    page.insert_text(fitz.Point(75, 239), "ITEMS / SERVICES", fontsize=8.5, color=(1, 1, 1))
    page.insert_text(fitz.Point(260, 239), "HSN/SAC", fontsize=8.5, color=(1, 1, 1))
    page.insert_text(fitz.Point(315, 239), "QTY", fontsize=8.5, color=(1, 1, 1))
    page.insert_text(fitz.Point(345, 239), "UNIT", fontsize=8.5, color=(1, 1, 1))
    page.insert_text(fitz.Point(380, 239), "RATE (₹)", fontsize=8.5, color=(1, 1, 1))
    page.insert_text(fitz.Point(435, 239), "DISC", fontsize=8.5, color=(1, 1, 1))
    page.insert_text(fitz.Point(470, 239), "TAX %", fontsize=8.5, color=(1, 1, 1))
    page.insert_text(fitz.Point(505, 239), "AMOUNT (₹)", fontsize=8.5, color=(1, 1, 1))

    # Table Row 1
    page.draw_rect(fitz.Rect(40, 245, 555, 280), color=(0.9, 0.9, 0.9), fill=(1, 1, 1))
    page.insert_text(fitz.Point(50, 260), "1", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(75, 260), "ASUS VivoBook 15 Laptop (Core i3, 8GB/512GB)", fontsize=8.5, fontname="helv", color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(265, 260), "8471", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(320, 260), "10", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(345, 260), "piece", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(380, 260), "38,999.00", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(440, 260), "-", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(475, 260), "18%", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(500, 260), "4,60,188.20", fontsize=8.5, color=(0.1, 0.1, 0.1))

    # Table Row 2
    page.draw_rect(fitz.Rect(40, 280, 555, 315), color=(0.9, 0.9, 0.9), fill=(0.98, 0.98, 0.99))
    page.insert_text(fitz.Point(50, 295), "2", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(75, 295), "Logitech MX Master 3S Wireless Mouse (8000 DPI)", fontsize=8.5, fontname="helv", color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(265, 295), "8471", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(320, 295), "10", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(345, 295), "piece", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(380, 295), "8,495.00", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(440, 295), "-", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(475, 295), "18%", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(500, 295), "1,00,241.00", fontsize=8.5, color=(0.1, 0.1, 0.1))

    # Table Row 3
    page.draw_rect(fitz.Rect(40, 315, 555, 350), color=(0.9, 0.9, 0.9), fill=(1, 1, 1))
    page.insert_text(fitz.Point(50, 330), "3", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(75, 330), "Boat Airdopes 141 Wireless Earbuds (42H Playback)", fontsize=8.5, fontname="helv", color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(265, 330), "8518", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(320, 330), "20", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(345, 330), "piece", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(380, 330), "1,299.00", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(440, 330), "-", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(475, 330), "18%", fontsize=8.5, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(505, 330), "30,656.40", fontsize=8.5, color=(0.1, 0.1, 0.1))

    # GST Tax Breakdown Table
    page.insert_text(fitz.Point(40, 375), "TAX BREAKDOWN", fontsize=9.5, fontname="helv", color=(0.2, 0.3, 0.6))
    page.draw_rect(fitz.Rect(40, 385, 555, 403), color=(0.4, 0.4, 0.4), fill=(0.92, 0.93, 0.95))
    page.insert_text(fitz.Point(45, 397), "HSN/SAC", fontsize=8, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(120, 397), "Taxable Value (₹)", fontsize=8, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(220, 397), "CGST Rate", fontsize=8, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(290, 397), "CGST Amt (₹)", fontsize=8, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(375, 397), "SGST Rate", fontsize=8, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(445, 397), "SGST Amt (₹)", fontsize=8, color=(0.1, 0.1, 0.1))
    page.insert_text(fitz.Point(510, 397), "Total Tax (₹)", fontsize=8, color=(0.1, 0.1, 0.1))

    page.draw_rect(fitz.Rect(40, 403, 555, 420), color=(0.9, 0.9, 0.9), fill=(1, 1, 1))
    page.insert_text(fitz.Point(45, 415), "8471", fontsize=8, color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(120, 415), "4,74,940.00", fontsize=8, color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(225, 415), "9.00%", fontsize=8, color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(295, 415), "42,744.60", fontsize=8, color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(380, 415), "9.00%", fontsize=8, color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(450, 415), "42,744.60", fontsize=8, color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(510, 415), "85,489.20", fontsize=8, color=(0.2, 0.2, 0.2))

    # Totals Summary Box (Right aligned)
    page.draw_rect(fitz.Rect(350, 440, 555, 530), color=(0.8, 0.8, 0.8), fill=(0.97, 0.97, 0.98))
    page.insert_text(fitz.Point(360, 460), "Subtotal:", fontsize=9.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(475, 460), "₹ 5,00,920.00", fontsize=9.5, color=(0.1, 0.1, 0.1))

    page.insert_text(fitz.Point(360, 480), "Total GST:", fontsize=9.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(475, 480), "₹ 90,165.60", fontsize=9.5, color=(0.1, 0.1, 0.1))

    page.draw_line(fitz.Point(355, 495), fitz.Point(550, 495), color=(0.7, 0.7, 0.7), width=1)
    page.insert_text(fitz.Point(360, 515), "Total Amount:", fontsize=11, fontname="helv", color=(0.1, 0.15, 0.4))
    page.insert_text(fitz.Point(465, 515), "₹ 5,91,085.60", fontsize=11, fontname="helv", color=(0.1, 0.15, 0.4))

    # Left box: Terms and Conditions
    page.insert_text(fitz.Point(40, 455), "Terms and Conditions", fontsize=9.5, fontname="helv", color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(40, 470), "1. Payment due within 7 days of invoice date.", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(40, 482), "2. Goods once sold will not be taken back.", fontsize=8.5, color=(0.3, 0.3, 0.3))
    page.insert_text(fitz.Point(40, 494), "3. Subject to Mumbai jurisdiction.", fontsize=8.5, color=(0.3, 0.3, 0.3))

    page.insert_text(fitz.Point(40, 520), "Notes", fontsize=9.5, fontname="helv", color=(0.2, 0.2, 0.2))
    page.insert_text(fitz.Point(40, 535), "Bulk order for new employee onboarding kits - laptops, mice, and wireless earbuds for Q3 hiring batch.", fontsize=8.5, color=(0.3, 0.3, 0.3))

    # Total Amount in Words Banner
    page.draw_rect(fitz.Rect(40, 550, 555, 575), color=(0.85, 0.9, 0.98), fill=(0.94, 0.96, 1.0))
    page.insert_text(fitz.Point(50, 566), "Total Amount (in words): FIVE LAKH NINETY-ONE THOUSAND AND EIGHTY-SIX RUPEES ONLY", fontsize=8.5, fontname="helv", color=(0.1, 0.2, 0.5))

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes
