"""Tiny two-language (vi/en) string catalog and helpers.

The site is Vietnamese by default; English is opt-in via a ``lang`` cookie and the
header toggle (see ``routers/pages.py`` and the middleware in ``main.py``). Rather
than pull in gettext/Babel for one extra language, translatable copy lives here as
a flat ``key -> {vi, en}`` map and is looked up with :func:`t`.

Templates get a request-bound ``t()`` / ``current_lang()`` / ``seat_label()`` (see
``templates/__init__.py``); server code (emails, seat-map JSON, HTTP errors) calls
the plain functions with an explicit ``lang``.
"""
from __future__ import annotations

LANGS = ("vi", "en")
DEFAULT_LANG = "vi"


def normalize(lang: str | None) -> str:
    """Coerce anything into one of the supported languages (default vi)."""
    return lang if lang in LANGS else DEFAULT_LANG


# --- catalog -----------------------------------------------------------------
# One entry per translatable string. Proper names (the concert name, the tier
# names, "Nắng Hoàng Hôn 2026") are deliberately NOT here — they read the same in
# both languages. Long-form prose (the two homepage essays) is kept as whole
# paragraphs so a translator sees the full sentence, not fragments.

STRINGS: dict[str, dict[str, str]] = {
    # ---- shared chrome (base.html) ----
    "nav.language": {"vi": "Ngôn ngữ", "en": "Language"},
    "social.fb_aria": {"vi": "Nắng Hoàng Hôn trên Facebook", "en": "Nắng Hoàng Hôn on Facebook"},
    "social.fb_title": {"vi": "Theo dõi trên Facebook", "en": "Follow on Facebook"},
    "social.yt_aria": {"vi": "Nắng Hoàng Hôn trên YouTube", "en": "Nắng Hoàng Hôn on YouTube"},
    "social.yt_title": {"vi": "Xem trên YouTube", "en": "Watch on YouTube"},
    "footer.phone": {"vi": "Số điện thoại", "en": "Phone"},

    # ---- common words reused across pages ----
    "common.order_code": {"vi": "Mã đơn hàng", "en": "Order code"},
    "common.amount": {"vi": "Số tiền", "en": "Amount"},
    "common.seats": {"vi": "ghế", "en": "seats"},
    "common.back_home": {"vi": "Về trang chủ", "en": "Back to home"},

    # ---- home hero ----
    "hero.cta": {"vi": "Đặt vé", "en": "Book tickets"},
    "hero.scroll": {"vi": "Xem thêm", "en": "Scroll down"},
    "hero.where1": {"vi": "PHÒNG HOÀ NHẠC LỚN", "en": "GRAND CONCERT HALL"},
    "hero.where2": {
        "vi": "HỌC VIỆN ÂM NHẠC QUỐC GIA VIỆT NAM",
        "en": "VIETNAM NATIONAL ACADEMY OF MUSIC",
    },

    # ---- in-page section nav ----
    "sectionnav.aria": {"vi": "Nội dung trang", "en": "Page contents"},
    "nav.about": {"vi": "Về chương trình", "en": "About"},
    "nav.program": {"vi": "Chương trình", "en": "Programme"},
    "nav.tickets": {"vi": "Đặt vé", "en": "Book tickets"},

    # ---- intro section (#gioi-thieu) ----
    "intro.title": {
        "vi": "Về chương trình Nắng Hoàng Hôn",
        "en": "About Nắng Hoàng Hôn",
    },
    "intro.p1": {
        "vi": "Nắng Hoàng Hôn là chuỗi hòa nhạc từ thiện ra đời năm 2024, lấy cảm "
              "hứng từ cuốn “Hồi ký chạy thận” của tác giả Hồ Hồng Việt, người đã "
              "kiên cường chiến đấu với bạo bệnh trong suốt 17 năm. Cuốn sách không "
              "chỉ là một hành trình cá nhân, mà còn là ngọn lửa thắp lên một chuỗi "
              "hòa nhạc mang sứ mệnh nhân văn: dùng âm nhạc làm cầu nối đưa con người "
              "đến với những giá trị sống tốt đẹp, đồng thời xoa dịu và nâng đỡ những "
              "mảnh đời bệnh nhân chạy thận nhân tạo có hoàn cảnh khó khăn.",
        "en": "Nắng Hoàng Hôn (“Sunset Glow”) is a charity concert series born in "
              "2024, inspired by the memoir “Hồi ký chạy thận” (A Dialysis Memoir) by "
              "Hồ Hồng Việt, who fought a grave illness with unwavering courage for "
              "seventeen years. The book is not only a personal journey but also the "
              "spark that lit a concert series with a humane mission: to make music a "
              "bridge that leads people toward the good things in life, while easing "
              "and lifting up the lives of dialysis patients facing hardship.",
    },
    "intro.p2": {
        "vi": "Trên hành trình hai năm đầu tiên đầy ý nghĩa, Nắng Hoàng Hôn đã khẳng "
              "định uy tín và nhận được sự quan tâm, yêu mến của khán giả khi kết nối "
              "hàng nghìn tâm hồn qua những giai điệu và câu chuyện chân thật. Từ "
              "những bước khởi đầu đầy ấm áp vào năm 2024, cho đến đêm nhạc “Những Lá "
              "Thư Chưa Gửi” năm 2025, Nắng Hoàng Hôn đã nhận được sự quyên góp, ủng "
              "hộ của đông đảo khán giả, nhà hảo tâm để chung tay tiếp sức và hỗ trợ "
              "cho các bệnh nhân chạy thận có hoàn cảnh khó khăn tại Hà Nội.",
        "en": "Across its first two meaningful years, Nắng Hoàng Hôn has earned its "
              "reputation and won the affection of audiences, connecting thousands of "
              "hearts through honest melodies and true stories. From its warm "
              "beginnings in 2024 to the 2025 concert “Những Lá Thư Chưa Gửi” (The "
              "Unsent Letters), Nắng Hoàng Hôn has drawn donations and support from a "
              "wide community of audience members and benefactors, joining hands to "
              "give strength and support to dialysis patients facing hardship in "
              "Hanoi.",
    },
    "intro.p3": {
        "vi": "Khác với những buổi hòa nhạc cổ điển truyền thống, đêm nhạc của mỗi mùa "
              "được thiết kế như một chuyến viễn du của tâm thức, để mỗi khán giả bước "
              "ra khỏi khán phòng đều mang theo một sự thay đổi tích cực từ sâu bên "
              "trong.",
        "en": "Unlike a traditional classical concert, each season’s performance is "
              "designed as a voyage of the mind, so that every audience member leaves "
              "the hall carrying a positive change from deep within.",
    },
    "intro.p4": {
        "vi": "Năm nay, Nắng Hoàng Hôn được vang lên tại Phòng hòa nhạc lớn – Học viện "
              "Âm nhạc Quốc gia Việt Nam, chủ đề Sông Trời sẽ là sự giao thoa của nghệ "
              "thuật âm nhạc, kể chuyện và hình ảnh, được thể hiện bởi các nghệ sĩ tài "
              "năng trong nước và quốc tế. Vượt lên trên một không gian thưởng thức "
              "nghệ thuật, phần quyên góp từ khán giả của buổi hòa nhạc sẽ tiếp tục sứ "
              "mệnh của Nắng Hoàng Hôn, thắp lên hy vọng sống cao đẹp cho các bệnh nhân "
              "chạy thận đang ngày đêm chiến đấu với bệnh tật.",
        "en": "This year Nắng Hoàng Hôn sounds at the Grand Concert Hall of the "
              "Vietnam National Academy of Music. Its theme, Sông Trời (“River of the "
              "Sky”), interweaves music, storytelling and imagery, brought to life by "
              "gifted artists from Vietnam and abroad. More than a space to enjoy art, "
              "the audience’s donations will carry on Nắng Hoàng Hôn’s mission, "
              "kindling a noble hope to live in the dialysis patients who battle their "
              "illness day and night.",
    },

    # ---- foreword section (#loi-ngo) ----
    "loingo.title": {"vi": "Nắng Hoàng Hôn 2026", "en": "Nắng Hoàng Hôn 2026"},
    "loingo.p1": {
        "vi": "Con người sinh ra và lớn lên bên bờ nước. Những dòng sông chảy qua thơ "
              "ca của Đỗ Phủ, Apollinaire hay Nguyễn Du... chưa bao giờ ngừng trôi "
              "trong tâm tưởng nhân loại. Để rồi, Langston Hughes phải thốt lên:",
        "en": "Humankind is born and grows up beside the water. The rivers that flow "
              "through the poetry of Du Fu, Apollinaire and Nguyễn Du… have never "
              "stopped flowing through the human mind. Until, at last, Langston Hughes "
              "was moved to say:",
    },
    "loingo.quote": {
        "vi": "Tôi biết những dòng sông cổ xưa như thế giới và già hơn dòng huyết quản "
              "của loài người. Linh hồn tôi đã trở nên sâu thẳm như những dòng sông…",
        # Hughes' original English (public domain, first published 1921).
        "en": "I’ve known rivers ancient as the world and older than the flow of human "
              "blood in human veins. My soul has grown deep like the rivers…",
    },
    "loingo.p2": {
        "vi": "Ta tựa vào sông để sống, gửi vào lòng sông những rực rỡ thành quách, cả "
              "những nỗi buồn, hy vọng và kỷ niệm. Sông bao dung nhận lấy, âm thầm cho "
              "đi, bốc hơi thành mây trời rồi lại trở về che chở thế gian dưới những "
              "hình hài khác.",
        "en": "We lean on the river to live, entrusting to its depths our glittering "
              "citadels, and our sorrows, hopes and memories alike. The river receives "
              "them with grace, gives quietly in return, rises into the clouds and "
              "comes back to shelter the world in other forms.",
    },
    "loingo.p3": {
        "vi": "Sông Trời là khúc ca được dệt nên từ hành trình vạn dặm của một Giọt "
              "nước – kẻ viễn du đi xuyên qua các nền văn minh và chạm vào muôn vàn số "
              "phận. Đêm nhạc là một lời tự sự dịu dàng về mối duyên nợ thiêng liêng "
              "giữa con người và thiên nhiên: nơi những gì ta gửi đi, rốt cuộc, lại là "
              "những gì ta nhận về.",
        "en": "Sông Trời is a song woven from the ten-thousand-mile journey of a single "
              "Drop of water — a traveller passing through civilisations and touching "
              "countless fates. The concert is a gentle reflection on the sacred bond "
              "between humankind and nature: where what we give away is, in the end, "
              "what we receive back.",
    },

    # ---- programme chapters (#chuong-trinh) ----
    "program.title": {"vi": "Chương trình", "en": "Programme"},
    "chapter.1.num": {"vi": "Chương I", "en": "Chapter I"},
    "chapter.2.num": {"vi": "Chương II", "en": "Chapter II"},
    "chapter.3.num": {"vi": "Chương III", "en": "Chapter III"},
    "chapter.4.num": {"vi": "Chương IV", "en": "Chapter IV"},
    "chapter.5.num": {"vi": "Chương V", "en": "Chapter V"},
    "chapter.1.name": {"vi": "KHỞI NGUỒN", "en": "THE SOURCE"},
    "chapter.2.name": {"vi": "CON NGƯỜI", "en": "HUMANKIND"},
    "chapter.3.name": {"vi": "GIỌT NƯỚC CHẠM TỚI CON NGƯỜI", "en": "THE DROP MEETS HUMANKIND"},
    "chapter.4.name": {"vi": "TÀN PHÁ", "en": "DESTRUCTION"},
    "chapter.5.name": {"vi": "TÁI SINH", "en": "REBIRTH"},

    # ---- seat map page (tickets.html) ----
    "tickets.title": {"vi": "Chọn ghế", "en": "Choose your seats"},
    "tickets.intro": {
        "vi": "Bấm vào ghế còn trống để chọn. Sân khấu ở phía dưới sơ đồ. Cuộn chuột "
              "để phóng to / thu nhỏ, kéo để di chuyển.",
        "en": "Tap an available seat to select it. The stage is at the bottom of the "
              "map. Scroll to zoom, drag to pan.",
    },
    "tickets.free": {"vi": "trống", "en": "available"},
    "tickets.booked": {"vi": "Đã đặt", "en": "Booked"},
    "tickets.selected": {"vi": "Ghế đã chọn", "en": "Selected seats"},
    "tickets.none_selected": {"vi": "Chưa chọn ghế nào.", "en": "No seats selected yet."},
    "tickets.continue": {"vi": "Tiếp tục", "en": "Continue"},
    "tickets.pay_later": {
        "vi": "Thanh toán sẽ được kết nối ở bước sau.",
        "en": "Payment follows in the next step.",
    },
    "tickets.loading": {"vi": "Đang tải sơ đồ…", "en": "Loading the seat map…"},
    "tickets.zoom_group": {"vi": "Phóng to thu nhỏ", "en": "Zoom"},
    "tickets.zoom_in": {"vi": "Phóng to", "en": "Zoom in"},
    "tickets.zoom_out": {"vi": "Thu nhỏ", "en": "Zoom out"},
    "tickets.zoom_reset": {"vi": "Xem toàn cảnh", "en": "Fit to view"},

    # ---- seat map graphics (seatmap.js JSON) ----
    "map.stage": {"vi": "SÂN KHẤU", "en": "STAGE"},
    "map.door": {"vi": "Cửa", "en": "Door"},
    "map.wall": {"vi": "Tường", "en": "Wall"},
    "map.column": {"vi": "Cột", "en": "Column"},
    "js.map_load_error": {
        "vi": "Không tải được sơ đồ chỗ ngồi.",
        "en": "Could not load the seat map.",
    },
    "js.max_seats": {
        # {n} substituted client-side.
        "vi": "Bạn chỉ có thể chọn tối đa {n} ghế mỗi lần.",
        "en": "You can select at most {n} seats per order.",
    },
    "js.seat_taken": {
        "vi": "Ghế này vừa được người khác chọn.",
        "en": "This seat was just taken by someone else.",
    },
    "js.hold_failed": {
        "vi": "Không giữ được ghế, vui lòng thử lại.",
        "en": "Could not hold the seat, please try again.",
    },
    "js.seat_summary": {
        # {n} seats, {total} formatted price — substituted client-side.
        "vi": "{n} ghế — {total}",
        "en": "{n} seats — {total}",
    },

    # ---- checkout form (checkout.html) ----
    "checkout.title": {"vi": "Thanh toán", "en": "Checkout"},
    "checkout.h1": {"vi": "Xác nhận & thanh toán", "en": "Confirm & pay"},
    "checkout.subtotal": {"vi": "Tạm tính", "en": "Subtotal"},
    "checkout.discount": {"vi": "Ưu đãi mở bán sớm", "en": "Early-bird discount"},
    "checkout.total": {"vi": "Tổng thanh toán", "en": "Total to pay"},
    "checkout.total_plain": {"vi": "Tổng cộng", "en": "Total"},
    "checkout.hold_note": {
        "vi": "Ghế của bạn được giữ tạm thời. Vui lòng hoàn tất thanh toán trong thời "
              "gian quy định, nếu không ghế sẽ được mở lại.",
        "en": "Your seats are held temporarily. Please complete payment within the "
              "time limit, or the seats will be released.",
    },
    "checkout.buyer": {"vi": "Thông tin người mua", "en": "Buyer details"},
    "checkout.name": {"vi": "Họ và tên", "en": "Full name"},
    "checkout.email": {"vi": "Email (nhận vé điện tử)", "en": "Email (for your e-ticket)"},
    "checkout.phone": {"vi": "Số điện thoại", "en": "Phone number"},
    "checkout.pay": {"vi": "Tiến hành thanh toán →", "en": "Proceed to payment →"},
    "checkout.back": {"vi": "← Quay lại chọn ghế", "en": "← Back to seat selection"},

    # ---- checkout success (checkout_success.html) ----
    "success.title": {"vi": "Đơn hàng", "en": "Order"},
    "success.paid_h1": {"vi": "🎉 Thanh toán thành công!", "en": "🎉 Payment successful!"},
    "success.paid_thanks": {
        "vi": "Cảm ơn bạn đã ủng hộ đêm nhạc gây quỹ. Vé điện tử sẽ được gửi tới",
        "en": "Thank you for supporting our charity concert. Your e-ticket will be sent to",
    },
    "success.pending_h1": {"vi": "Đang xác nhận thanh toán…", "en": "Confirming your payment…"},
    "success.pending_p": {
        "vi": "Chúng tôi đang chờ xác nhận từ cổng thanh toán. Trang này sẽ tự động cập "
              "nhật khi thanh toán hoàn tất.",
        "en": "We’re waiting for confirmation from the payment gateway. This page will "
              "update automatically once payment completes.",
    },
    "success.status": {"vi": "Trạng thái", "en": "Status"},

    # ---- checkout cancel (checkout_cancel.html) ----
    "cancel.title": {"vi": "Đã hủy đơn hàng", "en": "Order cancelled"},
    "cancel.h1": {"vi": "Đơn hàng đã được hủy", "en": "Your order has been cancelled"},
    "cancel.p": {
        "vi": "Ghế bạn giữ đã được mở lại cho người khác. Bạn có thể chọn ghế và thử "
              "lại bất cứ lúc nào.",
        "en": "The seats you were holding have been released. You can choose seats and "
              "try again any time.",
    },
    "cancel.retry": {"vi": "Chọn ghế lại", "en": "Choose seats again"},

    # ---- dev-pay simulator (checkout_devpay.html) ----
    "devpay.title": {"vi": "[DEV] Mô phỏng thanh toán", "en": "[DEV] Payment simulator"},
    "devpay.banner": {
        "vi": "⚙️ Chế độ phát triển — payOS chưa được cấu hình. Đây là trang mô phỏng "
              "thanh toán để kiểm thử.",
        "en": "⚙️ Development mode — payOS is not configured. This is a payment "
              "simulator for testing.",
    },
    "devpay.h1": {"vi": "Mô phỏng thanh toán", "en": "Payment simulator"},
    "devpay.explain": {
        "vi": "Trong môi trường thật, người mua sẽ được chuyển tới cổng payOS. Ở đây "
              "bạn có thể giả lập kết quả:",
        "en": "In production the buyer is redirected to payOS. Here you can simulate "
              "the outcome:",
    },
    "devpay.ok": {"vi": "✅ Giả lập: Thanh toán thành công", "en": "✅ Simulate: payment successful"},
    "devpay.cancel": {"vi": "✖ Giả lập: Hủy thanh toán", "en": "✖ Simulate: cancel payment"},

    # ---- e-ticket page (ticket.html) ----
    "ticket.title": {"vi": "Vé", "en": "Ticket"},
    "ticket.tagline": {
        "vi": "Đêm nhạc gây quỹ từ thiện — Hà Nội",
        "en": "Charity fundraising concert — Hanoi",
    },
    "ticket.class": {"vi": "Hạng vé", "en": "Ticket class"},
    "ticket.price": {"vi": "Giá", "en": "Price"},
    "ticket.code": {"vi": "Mã vé", "en": "Ticket code"},
    "ticket.buyer": {"vi": "Người mua", "en": "Buyer"},
    "ticket.order": {"vi": "Đơn hàng", "en": "Order"},
    "ticket.qr_alt": {"vi": "Mã QR vé", "en": "Ticket QR code"},
    "ticket.qr_note": {"vi": "Xuất trình mã này tại cửa vào", "en": "Show this code at the entrance"},

    # ---- HTTP errors (routers) ----
    "err.ticket_not_found": {"vi": "Không tìm thấy vé.", "en": "Ticket not found."},
    "err.order_not_found": {"vi": "Không tìm thấy đơn hàng.", "en": "Order not found."},
    "err.too_many_seats": {
        # {n} substituted server-side.
        "vi": "Bạn chỉ có thể giữ tối đa {n} ghế mỗi lần.",
        "en": "You can hold at most {n} seats at a time.",
    },
    "err.seat_taken": {
        "vi": "Ghế này vừa được người khác chọn.",
        "en": "This seat was just taken by someone else.",
    },

    # ---- confirmation email (services/tickets.py) ----
    "email.subject": {
        "vi": "[NẮNG HOÀNG HÔN 2026] XÁC NHẬN ĐĂNG KÝ VÉ THÀNH CÔNG",
        "en": "[NẮNG HOÀNG HÔN 2026] YOUR TICKET REGISTRATION IS CONFIRMED",
    },
    "email.greeting": {"vi": "Thân gửi Quý khán giả,", "en": "Dear guest,"},
    "email.intro": {
        "vi": "Lời đầu tiên, BTC Nắng Hoàng Hôn 2026 xin gửi lời cảm ơn sâu sắc tới quý "
              "vị vì đã quan tâm và dành thời gian tham dự Chương trình hoà nhạc từ "
              "thiện Nắng Hoàng Hôn 2026 – Sông Trời. Chúng tôi xin trân trọng thông "
              "báo:",
        "en": "First of all, the Nắng Hoàng Hôn 2026 organising team would like to "
              "express our heartfelt thanks for your interest in, and for taking the "
              "time to attend, the Nắng Hoàng Hôn 2026 – Sông Trời charity concert. We "
              "are delighted to confirm:",
    },
    "email.headline_comp": {
        "vi": "BẠN ĐÃ ĐĂNG KÝ VÉ MỜI THÀNH CÔNG!",
        "en": "YOUR COMPLIMENTARY TICKET IS CONFIRMED!",
    },
    "email.headline_sale": {
        "vi": "BẠN ĐÃ ĐĂNG KÝ ĐẶT VÉ VÀ QUYÊN GÓP THÀNH CÔNG!",
        "en": "YOUR TICKET AND DONATION ARE CONFIRMED!",
    },
    "email.registrant": {"vi": "Thông tin người đăng ký", "en": "Registration details"},
    "email.name": {"vi": "Họ và tên", "en": "Full name"},
    "email.seat_class": {"vi": "Hạng ghế", "en": "Seat class"},
    "email.ticket_type": {"vi": "Loại vé", "en": "Ticket type"},
    "email.comp_free": {"vi": "Vé mời (miễn phí)", "en": "Complimentary ticket (free)"},
    "email.comp_group": {"vi": "Vé mời", "en": "Complimentary"},
    "email.amount_label": {
        "vi": "Giá vé tương đương giá trị gây quỹ",
        "en": "Ticket price (donation value)",
    },
    "email.order_code": {"vi": "Mã đơn hàng", "en": "Order code"},
    "email.view_ticket": {"vi": "Xem vé", "en": "View ticket"},
    "email.qr_alt": {"vi": "Mã QR", "en": "QR code"},
    "email.your_tickets": {"vi": "Vé của bạn:", "en": "Your tickets:"},
    "email.update_note": {
        "vi": "BTC sẽ gửi email cập nhật thông tin chi tiết về buổi hòa nhạc trong thời "
              "gian ngắn tới. Quý khán giả vui lòng chú ý hòm mail.",
        "en": "The organising team will email you detailed information about the "
              "concert shortly. Please keep an eye on your inbox.",
    },
    "email.thanks": {
        "vi": "Thay mặt cho các bệnh nhân chạy thận nhân tạo được nhận sự hỗ trợ này, "
              "chúng tôi xin gửi lời tri ân sâu sắc đến bạn. Hành động tử tế của bạn "
              "không chỉ mang đến sự hỗ trợ thiết thực cho những mảnh đời đang gặp khó "
              "khăn mà còn lan tỏa thông điệp ý nghĩa về lòng nhân ái và sự sẻ chia. "
              "Mỗi đóng góp, dù là nhỏ bé nhất, đều góp phần tạo nên sự khác biệt to "
              "lớn, mang đến hy vọng và niềm tin cho những người đang phải chiến đấu "
              "với căn bệnh hiểm nghèo.",
        "en": "On behalf of the dialysis patients who will receive this support, we "
              "send you our deepest gratitude. Your kindness not only brings tangible "
              "help to lives in difficulty but also spreads a meaningful message of "
              "compassion and sharing. Every contribution, however small, helps make a "
              "great difference, bringing hope and faith to those fighting a serious "
              "illness.",
    },
    "email.closing": {"vi": "Xin chân thành cảm ơn!", "en": "With our sincere thanks!"},
    "email.signoff": {"vi": "BTC NẮNG HOÀNG HÔN 2026", "en": "THE NẮNG HOÀNG HÔN 2026 TEAM"},
    "email.contact": {"vi": "Thông tin liên hệ:", "en": "Contact:"},
    "email.hotline": {"vi": "Hotline", "en": "Hotline"},
    # Seat-line words: "{floor} Hàng {row} Ghế {n}".
    "seatline.row": {"vi": "Hàng", "en": "Row"},
    "seatline.seat": {"vi": "Ghế", "en": "Seat"},
}


def t(key: str, lang: str | None = DEFAULT_LANG, **fmt) -> str:
    """Look up ``key`` in ``lang``; fall back to Vietnamese, then the key itself.

    Extra keyword args are substituted with ``str.format`` (used for the ``{n}``
    placeholders in a few strings).
    """
    entry = STRINGS.get(key)
    if entry is None:
        return key
    lang = normalize(lang)
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    return text.format(**fmt) if fmt else text


def floor_name(section: str, lang: str | None = DEFAULT_LANG) -> str:
    """Translate a floor/section name: ``"Tầng 1" -> "Floor 1"`` in English.

    Anything not shaped like ``"Tầng N"`` (or Vietnamese output) is returned as-is,
    so unexpected section names never get mangled.
    """
    if normalize(lang) == "vi":
        return section
    parts = section.split()
    if len(parts) == 2 and parts[0] == "Tầng":
        return f"Floor {parts[1]}"
    return section


def seat_label(seat, lang: str | None = DEFAULT_LANG) -> str:
    """A seat's human label in ``lang``.

    Vietnamese reuses the stored ``seat.label`` verbatim; English is recomposed
    from the seat's parts (``"Floor 1 – Row A – Seat 12"``) so we never have to
    parse the stored Vietnamese string.
    """
    if normalize(lang) == "vi":
        return seat.label
    return (
        f"{floor_name(seat.section, 'en')} – "
        f"{t('seatline.row', 'en')} {seat.row_label} – "
        f"{t('seatline.seat', 'en')} {seat.seat_number}"
    )
