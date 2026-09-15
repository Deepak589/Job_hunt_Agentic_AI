// Two-column CV template — data-driven, renders from cv_data.json
// Palette sampled from the original main_cv.pdf: #2C3E50

#let d = json(sys.inputs.data)

#let ink      = rgb("#2C3E50")
#let sidetext = rgb("#DDE3E8")
#let sidehead = rgb("#8FA3B5")
#let sidemute = rgb("#B9C6D2")
#let body     = rgb("#1F2933")
#let muted    = rgb("#5A6978")
#let rule     = rgb("#C9D2DA")

#set page(paper: "a4", margin: 0pt)
#set text(font: "Carlito", size: 8.6pt, fill: body, lang: "en")
#set par(justify: true, leading: 0.52em, spacing: 0.62em)

#let SIDE = 32%

// ---------- sidebar pieces ----------
#let sidehead-block(t) = block(above: 11pt, below: 5pt)[
  #text(size: 8pt, weight: "bold", fill: sidehead, tracking: 0.5pt)[#upper(t)]
  #v(-4pt)
  #line(length: 100%, stroke: 0.5pt + sidehead.lighten(35%))
]

// DejaVu Sans carries these glyphs; Carlito does not. Text presentation, not emoji.
#let icon(kind) = {
  let g = (mail: "\u{2709}", phone: "\u{260E}", pin: "\u{25C6}", link: "\u{25C9}")
  text(font: "DejaVu Sans", size: 7.4pt, fill: sidemute, baseline: 0.4pt)[
    #g.at(kind, default: "\u{00B7}")
  ]
}

// Hyphenated terms must not break across lines: pdftotext drops the hyphen when they do,
// turning "fine-tuning" into "finetuning" and losing the ATS keyword. box() forbids the break.
#let keep-terms(s) = s.split(" ").map(w => if w.contains("-") { box(w) } else { w }).join(" ")

// ---------- main pieces ----------
#let mainhead(t) = block(above: 12pt, below: 5pt)[
  #text(size: 10.7pt, weight: "bold", fill: ink, tracking: 0.35pt)[#upper(t)]
  #v(-5pt)
  #line(length: 100%, stroke: 0.8pt + ink)
]

#let bullets(items) = {
  set par(justify: true, leading: 0.5em, spacing: 0.42em)
  for b in items {
    grid(columns: (7pt, 1fr), gutter: 0pt,
      text(fill: ink, size: 8pt)[•],
      text(size: 8.45pt)[#b])
  }
}

// ================= HEADER =================
#block(fill: ink, width: 100%, inset: (x: 20pt, y: 13pt))[
  #grid(columns: (1fr, 62pt), align: (horizon + left, horizon + right),
    [
      #text(size: 21pt, weight: "bold", fill: white, tracking: 1.6pt)[#d.name]
      #v(3pt)
      #text(size: 8.2pt, fill: sidemute, tracking: 2.4pt)[
        #d.tagline.map(upper).join(text(fill: sidehead)[ #h(3pt) · #h(3pt) ])
      ]
    ],
    box(clip: true, radius: 50%, stroke: 1.6pt + white.transparentize(55%),
        image(d.photo, width: 54pt, height: 54pt, fit: "cover"))
  )
]

// ================= BODY =================
#grid(columns: (SIDE, 1fr), rows: (1fr), gutter: 0pt,

  // ---------------- SIDEBAR ----------------
  block(fill: ink, width: 100%, height: 100%, inset: (x: 14pt, y: 13pt))[
    #set text(fill: sidetext, size: 7.7pt)
    #set par(justify: false, leading: 0.5em, spacing: 0.5em)
    #set text(hyphenate: false)

    #sidehead-block("Contact")
    #for c in d.contact {
      grid(columns: (11pt, 1fr), gutter: 0pt, icon(c.at(0)), text(size: 7.5pt)[#c.at(1)])
      v(1.4pt)
    }

    #sidehead-block("Technical Skills")
    #for s in d.skills {
      block(below: 5pt)[
        #text(weight: "bold", size: 7.7pt, fill: white)[#s.at(0):]
        #text(size: 7.4pt, fill: sidemute)[ #keep-terms(s.at(1))]
      ]
    }

    #sidehead-block("Education")
    #for e in d.education {
      block(below: 6pt)[
        #text(weight: "bold", size: 7.9pt, fill: white)[#e.at(0)] \
        #text(size: 7.3pt, fill: sidemute)[#e.at(1)] \
        #text(size: 7.1pt, fill: sidehead)[#e.at(2)]
      ]
    }

    #sidehead-block("Certifications")
    #for c in d.certifications {
      block(below: 3.6pt)[#text(size: 7.4pt, fill: sidemute)[#c]]
    }

    #sidehead-block("Languages")
    #for l in d.languages {
      block(below: 3.4pt)[
        #text(size: 7.5pt, fill: white)[#l.at(0)]
        #text(size: 7.3pt, fill: sidehead)[ – #l.at(1)]
      ]
    }
  ],

  // ---------------- MAIN ----------------
  block(width: 100%, inset: (x: 17pt, y: 13pt))[

    #mainhead("Profile")
    #text(size: 8.5pt)[#d.profile]

    #for section in d.main_sections {
      if section.kind == "projects" {
        mainhead("Projects")
        for p in section.items {
          block(below: 7.5pt, breakable: false)[
            #text(size: 9.3pt, weight: "bold", fill: ink)[#p.title]
            #v(1.2pt)
            #text(size: 7.4pt, fill: muted, style: "italic")[#p.meta]
            #v(2.6pt)
            #bullets(p.bullets)
          ]
        }
      } else if section.kind == "experience" {
        mainhead("Experience")
        for e in section.items {
          block(below: 7.5pt, breakable: false)[
            #grid(columns: (1fr, auto),
              text(size: 9pt, weight: "bold", fill: ink)[#e.title],
              text(size: 7.8pt, fill: muted)[#e.dates])
            #v(0.6pt)
            #text(size: 8.2pt, weight: "bold", fill: muted)[#e.org]
            #v(2.6pt)
            #bullets(e.bullets)
          ]
        }
      }
    }
  ]
)
