// templates/cover_letter.typ — data-driven, renders from a per-job JSON file
#let d = json(sys.inputs.data)

#set page(paper: "a4", margin: (x: 2.2cm, y: 2.4cm))
#set text(font: "Carlito", size: 10.5pt, lang: "en")
#set par(justify: true, leading: 0.65em)

#d.name \
#d.email · #d.phone · #d.location

#v(1.2em)
#datetime.today().display("[month repr:long] [day], [year]")

#v(1.2em)
Re: Application for #d.title at #d.company

#v(1em)
#d.body.split("\n\n").join([#v(0.8em)])
