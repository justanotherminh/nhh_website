"""The 2026 line-up: who performs, and a short biography for each.

Content, not UI copy, which is why it lives here rather than in ``i18n.STRINGS`` —
that catalog is a flat map of short interface strings, and eighteen paragraph-length
biographies in two languages would swamp it.

The biographies are condensed from the organisers' artist dossier. They are
summaries, not translations of it: the source runs to several hundred words per
artist, which nobody reads on a concert page, and each claim here is a factual one
about a real person's career — so each is kept short enough to be checked. The full
dossier remains the record for the printed programme.

Names are never translated. Instrument names are the same word in both languages
for most of the line-up; only the spoken roles genuinely differ.
"""
from __future__ import annotations

from app import i18n


def _r(vi: str, en: str | None = None) -> dict[str, str]:
    """A role label. Most are identical in both languages."""
    return {"vi": vi, "en": en if en is not None else vi}


VIOLIN = _r("Violin")
VIOLA = _r("Viola")
CELLO = _r("Cello")
PIANO = _r("Piano")
OBOE = _r("Oboe")
SOPRANO = _r("Soprano")
BARITONE = _r("Baritone")
TENOR = _r("Tenor")

# Each group: a heading, then its members. `bio` is None for the players the
# dossier lists by name only — better an honest name-and-instrument line than an
# invented biography.
GROUPS: list[dict] = [
    {
        "title": _r("Nghệ sĩ biểu diễn", "Performers"),
        "members": [
            {
                "name": "Hoàng Hồ Khánh Vân",
                "role": VIOLIN,
                "bio": {
                    "vi": "Một trong những nghệ sĩ violin năng động nhất Hà Nội. Bắt đầu "
                          "học violin từ 4 tuổi rưỡi, cô tốt nghiệp Thạc sĩ loại xuất sắc "
                          "tại Học viện Âm nhạc Liszt Ferenc (Hungary) theo học bổng "
                          "Stipendium Hungaricum. Giải Nhất Cuộc thi Violin Quốc tế "
                          "Kazakhstan lần thứ 10 (2023) và giải Dohnányi tại Cuộc thi Hòa "
                          "tấu Quốc gia Hungary (2018). Hiện là giảng viên violin tại Học "
                          "viện Âm nhạc Quốc gia Việt Nam và thành viên Hanoi Philharmonic "
                          "Orchestra.",
                    "en": "One of Hanoi's most active violinists. She began at four and a "
                          "half and completed her master's with distinction at the Liszt "
                          "Ferenc Academy of Music in Hungary on a Stipendium Hungaricum "
                          "scholarship. First prize at the 10th Kazakhstan International "
                          "Violin Competition (2023) and the Dohnányi Prize at Hungary's "
                          "national chamber music competition (2018). She teaches violin at "
                          "the Vietnam National Academy of Music and plays with the Hanoi "
                          "Philharmonic Orchestra.",
                },
            },
            {
                "name": "Lương Khánh Nhi",
                "role": PIANO,
                "bio": {
                    "vi": "Giải Ba và Huy chương Đồng Lady Roslyn Lyons tại Cuộc thi Piano "
                          "Quốc tế Leeds 2024. Cô nhận tài trợ từ Quỹ Borletti-Buitoni Trust "
                          "và là học giả của Imogen Cooper Music Trust; album đầu tay do "
                          "Signum Records phát hành mùa thu 2026. Mùa diễn 2025/26 đánh dấu "
                          "những lần đầu ra mắt tại Wigmore Hall (Luân Đôn), Lotte Concert "
                          "Hall (Seoul) và Phòng hoà nhạc Lớn — Học viện Âm nhạc Quốc gia "
                          "Việt Nam. Đồng sáng lập chuỗi hoà nhạc Piano Tết Nguyên Đán tôn "
                          "vinh các nhà soạn nhạc châu Á. Tiến sĩ Nghệ thuật Âm nhạc, Đại "
                          "học Michigan.",
                    "en": "Third prize and the Lady Roslyn Lyons Bronze Medal at the 2024 "
                          "Leeds International Piano Competition. She is a Borletti-Buitoni "
                          "Trust fellow and an Imogen Cooper Music Trust scholar, and her "
                          "debut album is released by Signum Records in autumn 2026. The "
                          "2025/26 season brings débuts at Wigmore Hall in London, Lotte "
                          "Concert Hall in Seoul and the Grand Concert Hall of the Vietnam "
                          "National Academy of Music. She co-founded the Lunar New Year "
                          "Piano Concert Series celebrating Asian composers, and holds a "
                          "doctorate from the University of Michigan.",
                },
            },
            {
                "name": "Hoàng Mạnh Lâm",
                "role": OBOE,
                "bio": {
                    "vi": "Bắt đầu học oboe từ năm 12 tuổi tại Học viện Âm nhạc Quốc gia "
                          "Việt Nam, nơi anh tốt nghiệp Thạc sĩ xuất sắc và hiện là giảng "
                          "viên. Từng tu nghiệp tại Temple University (Hoa Kỳ) theo học bổng "
                          "toàn phần, anh đã chơi trong Dàn nhạc Giao hưởng Việt Nam, "
                          "Thailand Philharmonic Orchestra và giữ vị trí bè phó oboe tại Sun "
                          "Symphony Orchestra. Giải Nhì Cuộc thi Âm nhạc Mùa thu 2019 (không "
                          "trao giải Nhất) và Giải Nhất năm 2024. Thành viên sáng lập dự án "
                          "thính phòng Schubert in a Mug.",
                    "en": "He took up the oboe at twelve at the Vietnam National Academy of "
                          "Music, where he later completed a master's with distinction and "
                          "now teaches. He studied at Temple University in the United States "
                          "on a full scholarship, and has played with the Vietnam National "
                          "Symphony Orchestra and the Thailand Philharmonic, and is "
                          "assistant principal oboe of the Sun Symphony Orchestra. Second "
                          "prize at the 2019 Autumn Music Competition, where no first prize "
                          "was awarded, and first prize in 2024. A founding member of the "
                          "chamber project Schubert in a Mug.",
                },
            },
            {
                "name": "Trần Viết Bảo",
                "role": PIANO,
                "bio": {
                    "vi": "Năm 2012 anh trở thành người Việt Nam đầu tiên được nhận vào Học "
                          "viện Âm nhạc Franz Liszt (Budapest), theo học hai giáo sư Attila "
                          "Némethy và Jenő Jandó. Giải Nhì cùng giải “Người chơi Nocturne "
                          "hay nhất” tại Cuộc thi Piano Quốc tế Việt Nam 2012, Huy chương "
                          "Vàng Festival Âm nhạc Quốc tế Cheonan (Hàn Quốc) và Giải Ba Cuộc "
                          "thi Piano Roma (2016). Từ 2019 anh theo học Tiến sĩ Biểu diễn "
                          "Piano tại Đại học Âm nhạc Quốc gia Bucharest, trở thành một trong "
                          "những tiến sĩ piano trẻ nhất Việt Nam.",
                    "en": "In 2012 he became the first Vietnamese pianist admitted to the "
                          "Franz Liszt Academy of Music in Budapest, studying with Attila "
                          "Némethy and Jenő Jandó. Second prize and Best Nocturne "
                          "Performance at the 2012 Vietnam International Piano Competition, "
                          "gold at the Cheonan International Music Festival in Korea and "
                          "third prize at the 2016 Rome Piano Competition. He has been a "
                          "doctoral candidate in piano performance at the National "
                          "University of Music Bucharest since 2019, making him one of "
                          "Vietnam's youngest piano doctorates.",
                },
            },
            {
                "name": "Hoàng Hồ Thu",
                "role": PIANO,
                "bio": {
                    "vi": "Pianist, giảng viên và người kể chuyện bằng âm nhạc. Cô tốt "
                          "nghiệp Thạc sĩ Biểu diễn Piano loại xuất sắc tại Học viện Âm nhạc "
                          "Liszt Ferenc (Hungary). Giải Ba Cuộc thi Piano Quốc tế Hà Nội "
                          "(2012), Giải Ba Cuộc thi Piano Quốc tế Barletta (Ý, 2017) và giải "
                          "Dohnányi tại Hungary (2018). Thành viên chủ chốt của nhóm "
                          "concert-talk Schubert in a Mug từ 2020, và từ 2024 cô mang tinh "
                          "thần ấy vào Nắng Hoàng Hôn — chuỗi hoà nhạc thường niên gây quỹ "
                          "cho bệnh nhân chạy thận. Hiện giảng dạy tại Khoa Piano, Học viện "
                          "Âm nhạc Quốc gia Việt Nam.",
                    "en": "A pianist, teacher and storyteller through music. She completed "
                          "her master's in piano performance with distinction at the Liszt "
                          "Ferenc Academy of Music in Hungary. Third prize at the Hanoi "
                          "International Piano Competition (2012) and at the Barletta "
                          "International Piano Competition in Italy (2017), and the "
                          "Dohnányi Prize in Hungary (2018). A core member of the "
                          "concert-talk group Schubert in a Mug since 2020, she brought that "
                          "same spirit to Nắng Hoàng Hôn in 2024 — the annual concert series "
                          "raising funds for dialysis patients. She teaches in the piano "
                          "faculty of the Vietnam National Academy of Music.",
                },
            },
            {
                "name": "Sviatlana Halubouskaya",
                "role": VIOLA,
                "bio": {
                    "vi": "Sinh ra tại Belarus, chị bắt đầu với violin từ năm 7 tuổi và "
                          "chuyển sang viola năm 15 tuổi, theo học tại Belarusian State "
                          "Academy of Music (Minsk). Giải Nhì cuộc thi E. Koka (Moldova, "
                          "2005) và Giải Ba cuộc thi M. Elsky (Minsk, 2008); từng học chuyên "
                          "sâu với Yuri Bashmet. Từ 2008 đến 2018 chị là nghệ sĩ viola độc "
                          "tấu thứ hai tại Nhà hát Bolshoi Belarus, và từ 2018 là trưởng bè "
                          "viola của Sun Symphony Orchestra tại Việt Nam.",
                    "en": "Born in Belarus, she started on the violin at seven and moved to "
                          "the viola at fifteen, training at the Belarusian State Academy of "
                          "Music in Minsk. Second prize at the E. Koka Competition in "
                          "Moldova (2005) and third at the M. Elsky Competition in Minsk "
                          "(2008), with masterclasses under Yuri Bashmet. From 2008 to 2018 "
                          "she was second solo viola at the Bolshoi Theatre of Belarus, and "
                          "since 2018 has been principal viola of the Sun Symphony Orchestra "
                          "in Vietnam.",
                },
            },
            {
                "name": "Lưu Ly Ly",
                "role": CELLO,
                "bio": {
                    "vi": "Bắt đầu học cello từ năm 11 tuổi. Sau Học viện Âm nhạc Quốc gia "
                          "Việt Nam, chị theo học chuyên ngành Biểu diễn Cello tại Nhạc viện "
                          "Rimsky-Korsakov Saint Petersburg (Nga). Chị từng biểu diễn tại Na "
                          "Uy, Nga và Thái Lan; năm 2022 độc tấu cùng Dàn nhạc Giao hưởng "
                          "Việt Nam trong Hoà nhạc Tài năng trẻ do UNFPA tổ chức. Hiện là "
                          "thành viên Dàn nhạc Giao hưởng Việt Nam, từng tham gia công diễn "
                          "vở opera Công nữ Anio tại Việt Nam và Nhật Bản (2023).",
                    "en": "She began the cello at eleven, studying at the Vietnam National "
                          "Academy of Music and then cello performance at the "
                          "Rimsky-Korsakov Saint Petersburg Conservatory in Russia. She has "
                          "performed in Norway, Russia and Thailand, and appeared as soloist "
                          "with the Vietnam National Symphony Orchestra in the 2022 Young "
                          "Talent Concert organised by UNFPA. She is a member of the Vietnam "
                          "National Symphony Orchestra and toured the opera Princess Anio in "
                          "Vietnam and Japan in 2023.",
                },
            },
            {
                "name": "Victoriia Filippova",
                "role": VIOLIN,
                "bio": {
                    "vi": "Nghệ sĩ violin người Nga, tốt nghiệp Nhạc viện Quốc gia "
                          "Tchaikovsky Moscow. Chị từng là thành viên Kunming International "
                          "Philharmonic (Trung Quốc) và Russian National Youth Symphony "
                          "Orchestra thuộc Moscow Philharmonic. Hiện là thành viên Sun "
                          "Symphony Orchestra tại Hà Nội, với niềm đam mê đặc biệt dành cho "
                          "âm nhạc thính phòng.",
                    "en": "A Russian violinist and graduate of the Tchaikovsky Moscow State "
                          "Conservatory. She has played with the Kunming International "
                          "Philharmonic in China and the Russian National Youth Symphony "
                          "Orchestra of the Moscow Philharmonic, and is now a member of the "
                          "Sun Symphony Orchestra in Hanoi, with a particular love of "
                          "chamber music.",
                },
            },
            {
                "name": "Tạ Khắc Huy",
                "role": PIANO,
                "bio": {
                    "vi": "Sinh năm 2003, anh bắt đầu học piano từ năm 5 tuổi và theo học "
                          "tại Học viện Âm nhạc Quốc gia Việt Nam dưới sự hướng dẫn của "
                          "PGS.TS Nguyễn Huy Phương. Năm 2023 anh giành Giải Nhất bảng C "
                          "Cuộc thi Piano TP. Hồ Chí Minh và Giải Nhì bảng C Cuộc thi Âm "
                          "nhạc Mùa thu. Nhận học bổng Toyota năm 2020 và 2023, anh từng "
                          "tham gia chương trình trao đổi tại Đại học Mälardalen (Thụy "
                          "Điển). Hiện là học viên cao học chuyên ngành Biểu diễn Piano.",
                    "en": "Born in 2003, he began the piano at five and studies at the "
                          "Vietnam National Academy of Music under Assoc. Prof. Nguyễn Huy "
                          "Phương. In 2023 he won first prize in category C at the Ho Chi "
                          "Minh City Piano Competition and second in category C at the "
                          "Autumn Music Competition. A Toyota scholar in 2020 and 2023, he "
                          "spent an exchange term at Mälardalen University in Sweden and is "
                          "now a master's student in piano performance.",
                },
            },
            {
                "name": "Phạm Hồng Ánh",
                "role": SOPRANO,
                "bio": {
                    "vi": "Thủ khoa đầu vào Khoa Thanh nhạc, Học viện Âm nhạc Quốc gia Việt "
                          "Nam (2022) và thủ khoa tốt nghiệp (2026). Á quân dòng nhạc thính "
                          "phòng Tiếng hát Hà Nội 2025 và Giải Nhất Liên hoan Âm nhạc Quốc "
                          "tế Crescendo 2026. Cô từng góp mặt trong các chương trình truyền "
                          "hình Tổ quốc tôi yêu và Bài ca không quên. Giọng soprano vừa nội "
                          "lực vừa trữ tình, cô hát được cả nhạc cổ điển, thính phòng lẫn "
                          "dân gian.",
                    "en": "She entered the vocal faculty of the Vietnam National Academy of "
                          "Music top of her intake in 2022 and graduated top of her class in "
                          "2026. Runner-up in the chamber category of Tiếng hát Hà Nội 2025 "
                          "and first prize at the Crescendo International Music Festival "
                          "2026. She has appeared on the television programmes Tổ quốc tôi "
                          "yêu and Bài ca không quên, and sings across classical, chamber "
                          "and Vietnamese folk repertoire.",
                },
            },
        ],
    },
    {
        "title": _r(
            "Cùng các học sinh, sinh viên Đoàn Thanh niên "
            "Học viện Âm nhạc Quốc gia Việt Nam",
            "With students of the Vietnam National Academy of Music Youth Union",
        ),
        "members": [
            {
                "name": "Đào Tiến Dũng",
                "role": BARITONE,
                "bio": {
                    "vi": "Tốt nghiệp Cử nhân Thanh nhạc với tổng điểm cao nhất khoa, Học "
                          "viện Âm nhạc Quốc gia Việt Nam; hiện theo học cao học dưới sự "
                          "hướng dẫn của TS. Nguyễn Khánh Ly. Anh từng được dìu dắt bởi NSND "
                          "Trần Hiếu và NSND Quang Thọ.",
                    "en": "He graduated top of the vocal faculty at the Vietnam National "
                          "Academy of Music and is now a master's student under Dr Nguyễn "
                          "Khánh Ly, having been mentored by People's Artists Trần Hiếu and "
                          "Quang Thọ.",
                },
            },
            {
                "name": "Đặng Yến Nhi",
                "role": PIANO,
                "bio": {
                    "vi": "Sinh năm 2006 tại Hà Nội, hiện theo học Khoa Piano — Học viện Âm "
                          "nhạc Quốc gia Việt Nam. Giải Nhì CEG Music Festival 2017 và Giải "
                          "Vàng Rising Stars International Arts Festival 2020. Cô đang đảm "
                          "nhiệm vị trí Phó Trưởng ban Chuyên môn CLB NEU Philharmonic.",
                    "en": "Born in Hanoi in 2006 and studying in the piano faculty of the "
                          "Vietnam National Academy of Music. Second prize at the CEG Music "
                          "Festival 2017 and gold at the Rising Stars International Arts "
                          "Festival 2020. She is deputy head of programming for the NEU "
                          "Philharmonic club.",
                },
            },
            {
                "name": "Nguyễn Hằng",
                "role": SOPRANO,
                "bio": {
                    "vi": "Sinh viên Khoa Thanh nhạc, Học viện Âm nhạc Quốc gia Việt Nam. "
                          "Giải Distinction tại Chicago International Music Competition & "
                          "Festival in Vietnam.",
                    "en": "A student in the vocal faculty of the Vietnam National Academy of "
                          "Music, and a Distinction Award winner at the Chicago "
                          "International Music Competition & Festival in Vietnam.",
                },
            },
            {
                "name": "Trần Gia Bách",
                "role": TENOR,
                "bio": {
                    "vi": "Sinh viên năm ba Khoa Thanh nhạc, Học viện Âm nhạc Quốc gia Việt "
                          "Nam, theo học NSND Dương Minh Đức. Anh từng tham gia Giọng hát "
                          "hay Hà Nội 2022.",
                    "en": "A third-year student in the vocal faculty of the Vietnam National "
                          "Academy of Music under People's Artist Dương Minh Đức, and a "
                          "competitor at Giọng hát hay Hà Nội 2022.",
                },
            },
            {"name": "Đặng Tiệp", "role": _r("Trống", "Percussion"), "bio": None},
            {"name": "Bảo Anh", "role": VIOLIN, "bio": None},
            {"name": "Thái Hoà", "role": VIOLIN, "bio": None},
            {"name": "Huyền Linh", "role": VIOLA, "bio": None},
            {"name": "Quốc Anh", "role": CELLO, "bio": None},
        ],
    },
    {
        "title": _r("Hợp xướng", "Chorus"),
        "members": [
            {
                "name": "Nguyễn Hải Yến",
                "role": _r("Chỉ huy hợp xướng", "Chorus conductor"),
                "bio": {
                    "vi": "Thủ khoa ngành Chỉ huy Giao hưởng và Thạc sĩ tại Học viện Âm "
                          "nhạc Quốc gia Việt Nam. Là người tiên phong phát triển hợp xướng "
                          "cộng đồng tại Việt Nam, cô từng giành Huy chương Vàng, Bạc và các "
                          "giải đặc biệt tại những Liên hoan Hợp xướng Quốc tế ở Đức và Việt "
                          "Nam. Cô sáng lập kiêm chỉ huy chính Hợp xướng Gió Xanh (2019) và "
                          "Hợp xướng Tuổi Vàng (2017) — dàn hợp xướng người cao tuổi đầu "
                          "tiên của Việt Nam — đồng thời là trợ lý chỉ huy cho nhạc trưởng "
                          "Honna Tetsuji tại Dàn nhạc Giao hưởng Việt Nam.",
                    "en": "She graduated top of her class in orchestral conducting and "
                          "completed a master's at the Vietnam National Academy of Music. A "
                          "pioneer of community choral singing in Vietnam, she has won gold "
                          "and silver medals and special prizes at international choral "
                          "festivals in Germany and Vietnam. She founded and directs the Gió "
                          "Xanh Choir (2019) and the Tuổi Vàng Choir (2017), Vietnam's first "
                          "choir for older singers, and is assistant conductor to Honna "
                          "Tetsuji at the Vietnam National Symphony Orchestra.",
                },
            },
            {
                "name": "Gió Xanh · NEU Philharmonic · Đoàn Thanh niên VNAM",
                "role": _r("Hợp xướng", "Chorus"),
                "bio": None,
            },
        ],
    },
    {
        "title": _r("Sáng tác & chuyển soạn", "Composer & arranger"),
        "members": [
            {
                "name": "Lê Bằng",
                "role": _r("Nhạc sĩ", "Composer"),
                "bio": {
                    "vi": "Sinh năm 1983, tốt nghiệp chuyên ngành Piano Jazz và Sáng tác tại "
                          "Học viện Âm nhạc Quốc gia Việt Nam, tu nghiệp tại Học viện Âm "
                          "nhạc Malmö (Thụy Điển) năm 2015. Tác phẩm giao hưởng Dòng chảy "
                          "nghìn năm của anh đoạt giải thưởng Hội Nhạc sĩ Việt Nam năm 2010 "
                          "và được Dàn nhạc Giao hưởng Rouen (Pháp) công diễn trong đại lễ "
                          "1000 năm Thăng Long – Hà Nội. Hiện anh giảng dạy Piano Jazz tại "
                          "Nhạc viện TP. Hồ Chí Minh và Sáng tác — phối khí tại Học viện Âm "
                          "nhạc Quốc gia Việt Nam.",
                    "en": "Born in 1983, he trained in jazz piano and composition at the "
                          "Vietnam National Academy of Music and studied further at the "
                          "Malmö Academy of Music in Sweden in 2015. His symphonic work Dòng "
                          "chảy nghìn năm won a Vietnam Musicians' Association prize in 2010 "
                          "and was performed by the Rouen Symphony Orchestra of France for "
                          "Hanoi's millennium celebrations. He teaches jazz piano at the Ho "
                          "Chi Minh City Conservatory, and composition and orchestration at "
                          "the Vietnam National Academy of Music.",
                },
            },
            {
                "name": "Nguyễn Thế Vinh",
                "role": _r("Chuyển soạn · Piano", "Arranger · Piano"),
                "bio": {
                    "vi": "Sinh năm 2000, anh đến với piano từ năm 5 tuổi. Huy chương Vàng "
                          "Asia International Piano Festival (2010), Giải Nhì Cuộc thi Piano "
                          "Quốc tế Việt Nam và ASEAN (2012), Giải Nhất Tài năng trẻ Steinway "
                          "Việt Nam (2016). Anh từng nhận học bổng toàn phần tại "
                          "Shattuck-St. Mary's School (Hoa Kỳ), và năm 2019 gây tiếng vang "
                          "tại Siêu trí tuệ Việt Nam với phần thi cảm âm piano bằng thị "
                          "giác. Tốt nghiệp Cao học tại Học viện Âm nhạc Quốc gia Việt Nam.",
                    "en": "Born in 2000, he came to the piano at five. Gold at the Asia "
                          "International Piano Festival (2010), second prize at the Vietnam "
                          "and ASEAN International Piano Competitions (2012) and first prize "
                          "at Steinway Vietnam Young Talent (2016). He held a full "
                          "scholarship at Shattuck-St. Mary's School in the United States, "
                          "and in 2019 drew national attention on Siêu trí tuệ Việt Nam for "
                          "identifying and reproducing music by sight alone. He holds a "
                          "master's from the Vietnam National Academy of Music.",
                },
            },
        ],
    },
    {
        "title": _r("Dẫn chương trình", "Host"),
        "members": [
            {
                "name": "Trần Tâm Trang",
                "role": _r("MC"),
                "bio": {
                    "vi": "Biên tập viên, phát thanh viên truyền hình và người dẫn chương "
                          "trình song ngữ Việt – Anh với hơn 10 năm kinh nghiệm. Tốt nghiệp "
                          "Cử nhân Quản trị Kinh doanh tại Đại học Troy (Hoa Kỳ), chị từng "
                          "sản xuất chương trình tại Truyền hình Quốc phòng Việt Nam và FPT "
                          "Telecom. Hiện là biên tập viên, người dẫn chương trình thời sự "
                          "tại Vietnam Today, đồng thời là huấn luyện viên giọng nói và đồng "
                          "sáng lập Inner World Music and Art Center.",
                    "en": "A television editor, presenter and bilingual Vietnamese–English "
                          "host with over ten years in media. She holds a business degree "
                          "from Troy University in the United States and has produced "
                          "programmes for Vietnam Defence Television and FPT Telecom. She "
                          "now edits and presents the news at Vietnam Today, and is a voice "
                          "coach and co-founder of the Inner World Music and Art Center.",
                },
            },
        ],
    },
]


def for_lang(lang: str) -> list[dict]:
    """The line-up with every label resolved to one language.

    Resolved here rather than in the template so the markup stays a plain loop
    instead of indexing dictionaries by language on every field.
    """
    lang = i18n.normalize(lang)
    return [
        {
            "title": group["title"][lang],
            "members": [
                {
                    "name": m["name"],
                    "role": m["role"][lang],
                    "bio": m["bio"][lang] if m["bio"] else None,
                }
                for m in group["members"]
            ],
        }
        for group in GROUPS
    ]
