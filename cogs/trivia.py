"""Dragon Ball trivia — a skill-based way to earn donuts.

`/trivia` serves a random Dragon Ball / Z / Super question with four buttons;
answer correctly before the timer to win donuts. A per-user cooldown keeps it
from becoming a pure faucet, and the option order is shuffled every time so the
answer isn't just "always the first button".
"""

from __future__ import annotations
import datetime as dt
import random
from typing import Any, Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from szofie import badges, ui
from szofie.combat import reset_generation
from szofie.plushies import plushie_perk


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


QUESTIONS: List[Tuple[str, List[str], int]] = [
    ("What is Goku's Saiyan birth name?", ["Kakarot", "Bardock", "Raditz", "Turles"], 0),
    ("Who is Goku's older brother?", ["Raditz", "Nappa", "Turles", "Broly"], 0),
    (
        "What is the Saiyan home planet that Frieza destroyed?",
        ["Planet Vegeta", "Namek", "Kanassa", "Yardrat"],
        0,
    ),
    ("How many Dragon Balls are needed to summon Shenron?", ["7", "5", "4", "10"], 0),
    (
        "What is Goku's signature energy-wave attack?",
        ["Kamehameha", "Galick Gun", "Final Flash", "Destructo Disc"],
        0,
    ),
    ("Which master first trains Goku and Krillin?", ["Master Roshi", "King Kai", "Korin", "Kami"], 0),
    ("What is the name of Vegeta and Bulma's son?", ["Trunks", "Goten", "Tarble", "Gohan"], 0),
    (
        "Which form does Goku first achieve against Frieza on Namek?",
        ["Super Saiyan", "Super Saiyan 2", "Super Saiyan God", "Kaio-ken"],
        0,
    ),
    ("On Namek, Piccolo fuses with which warrior Namekian?", ["Nail", "Dende", "Kami", "Guru"], 0),
    ("What is the Fusion Dance form of Goku and Vegeta?", ["Gogeta", "Vegito", "Gotenks", "Gometa"], 0),
    (
        "What is the Potara-earring fusion of Goku and Vegeta called?",
        ["Vegito", "Gogeta", "Gotenks", "Kefla"],
        0,
    ),
    (
        "Whose death triggers Goku's first Super Saiyan transformation?",
        ["Krillin", "Piccolo", "Gohan", "Yamcha"],
        0,
    ),
    ("Who created the Androids?", ["Dr. Gero", "Dr. Briefs", "Babidi", "Bulma"], 0),
    (
        "Which villain must absorb Androids 17 and 18 to reach his 'perfect' form?",
        ["Cell", "Majin Buu", "Frieza", "Broly"],
        0,
    ),
    ("Which wizard's ritual awakens Majin Buu in the Buu Saga?", ["Babidi", "Dabura", "Bibidi", "Beerus"], 0),
    ("Who is the God of Destruction of Universe 7?", ["Beerus", "Whis", "Champa", "Vados"], 0),
    ("Who is Beerus's attendant and martial-arts teacher?", ["Whis", "Vados", "Kibito", "Zeno"], 0),
    (
        "What is the red-haired divine form Goku first gains in Super?",
        ["Super Saiyan God", "Super Saiyan Blue", "Ultra Instinct", "Super Saiyan 4"],
        0,
    ),
    (
        "What is the blue-haired evolution of Super Saiyan God?",
        ["Super Saiyan Blue", "Super Saiyan 3", "Ultra Instinct", "Super Saiyan God"],
        0,
    ),
    (
        "What silver-haired state does Goku unlock in the Tournament of Power?",
        ["Ultra Instinct", "Super Saiyan Blue", "Super Saiyan 4", "Super Saiyan God"],
        0,
    ),
    ("What is the name of Earth's eternal dragon?", ["Shenron", "Porunga", "Icarus", "Zalama"], 0),
    ("What is the Namekian dragon called?", ["Porunga", "Shenron", "Icarus", "Ultimate Shenron"], 0),
    (
        "What is Goku's magic flying cloud called?",
        ["Flying Nimbus", "Power Pole", "Bansho Fan", "Magic Broom"],
        0,
    ),
    ("What is the name of Goku's wife?", ["Chi-Chi", "Bulma", "Videl", "Launch"], 0),
    ("Who is Gohan's wife?", ["Videl", "Chi-Chi", "Erasa", "Marron"], 0),
    ("What is the name of Gohan and Videl's daughter?", ["Pan", "Bra", "Marron", "Chi-Chi"], 0),
    (
        "What company does Bulma's family run?",
        ["Capsule Corporation", "Red Ribbon Army", "Satan Inc.", "Orange Star"],
        0,
    ),
    ("Who publicly takes credit for defeating Cell?", ["Mr. Satan", "Yamcha", "Tien", "Krillin"], 0),
    ("What is Goku and Chi-Chi's second son named?", ["Goten", "Gohan", "Trunks", "Turles"], 0),
    (
        "What technique does Goku use to finally destroy Kid Buu?",
        ["Spirit Bomb", "Dragon Fist", "Kamehameha", "Instant Transmission"],
        0,
    ),
    ("Who is Frieza's father?", ["King Cold", "Cooler", "Chilled", "Frost"], 0),
    ("Which Universe 6 Saiyan does Vegeta take on as a pupil?", ["Cabba", "Kale", "Hit", "Frost"], 0),
    ("Who is the Omni-King who rules over all universes?", ["Zeno", "Beerus", "Grand Priest", "Whis"], 0),
    (
        "What is Vegeta's royal title?",
        ["Prince of all Saiyans", "King of the Saiyans", "Saiyan Emperor", "Lord of Sadala"],
        0,
    ),
    ("What colour does a Super Saiyan's hair turn?", ["Gold", "Blue", "Red", "Silver"], 0),
    ("Who is Goku's firstborn son?", ["Gohan", "Goten", "Trunks", "Turles"], 0),
    ("Who does Krillin marry?", ["Android 18", "Android 17", "Chi-Chi", "Launch"], 0),
    ("What is the name of Krillin and Android 18's daughter?", ["Marron", "Pan", "Bulla", "Videl"], 0),
    ("What kind of animal is Beerus modelled after?", ["A cat", "A dog", "A lizard", "A bird"], 0),
    ("What race is Piccolo?", ["Namekian", "Saiyan", "Human", "Majin"], 0),
    (
        "What is the name of Frieza's elite five-member squad?",
        ["The Ginyu Force", "The Red Ribbon Army", "The Pride Troopers", "The Spice Boys"],
        0,
    ),
    (
        "What is Captain Ginyu's signature ability?",
        ["Swapping bodies with others", "Reading minds", "Turning invisible", "Multiplying himself"],
        0,
    ),
    (
        "Who becomes Earth's Guardian after Kami merges with Piccolo?",
        ["Dende", "Mr. Popo", "Nail", "Kibito"],
        0,
    ),
    ("Who is Goku's father?", ["Bardock", "Raditz", "King Vegeta", "Nappa"], 0),
    ("Which Saiyan is known as the 'Legendary Super Saiyan'?", ["Broly", "Vegeta", "Turles", "Bardock"], 0),
    ("What is the name of Vegeta and Bulma's daughter?", ["Bulla", "Pan", "Marron", "Chi-Chi"], 0),
    (
        "What technique lets Goku teleport instantly to a target?",
        ["Instant Transmission", "Kaio-ken", "Solar Flare", "Spirit Bomb"],
        0,
    ),
    (
        "Which alien race taught Goku the Instant Transmission technique?",
        ["The Yardrats", "The Namekians", "The Kais", "The Metamorans"],
        0,
    ),
    (
        "What is Krillin's signature spinning energy-disc attack?",
        ["Destructo Disc", "Kamehameha", "Masenko", "Final Flash"],
        0,
    ),
    (
        "What is Piccolo's signature drilling beam attack?",
        ["Special Beam Cannon", "Galick Gun", "Final Flash", "Big Bang Attack"],
        0,
    ),
    (
        "What is Vegeta's signature purple energy attack?",
        ["Galick Gun", "Kamehameha", "Special Beam Cannon", "Destructo Disc"],
        0,
    ),
    (
        "What is Master Roshi's island home called?",
        ["Kame House", "The Lookout", "Korin Tower", "Capsule Corp"],
        0,
    ),
    (
        "What health-restoring beans are grown atop Korin's Tower?",
        ["Senzu Beans", "Sacred Beans", "Dragon Beans", "Magic Beans"],
        0,
    ),
    (
        "On whose planet does Goku learn the Kaio-ken and the Spirit Bomb?",
        ["King Kai", "Korin", "Whis", "Beerus"],
        0,
    ),
    (
        "What is the name of the all-universe tournament in Dragon Ball Super?",
        ["Tournament of Power", "World Martial Arts Tournament", "Cell Games", "Budokai"],
        0,
    ),
    (
        "What superhero alter-ego does Gohan adopt in high school?",
        ["The Great Saiyaman", "Golden Warrior", "Saiyaman X", "Mr. Satan Jr."],
        0,
    ),
    ("What is the fused form of Trunks and Goten called?", ["Gotenks", "Gogeta", "Vegito", "Trunkten"], 0),
    (
        "Who is Bulma's father, the founder of Capsule Corporation?",
        ["Dr. Brief", "Dr. Gero", "Master Roshi", "Dr. Wheelo"],
        0,
    ),
]
F1_QUESTIONS: List[Tuple[str, List[str], int]] = [
    (
        "Which team did Juan Manuel Fangio win his first title (1951) with?",
        ["Alfa Romeo", "Maserati", "Ferrari", "Mercedes"],
        0,
    ),
    (
        "Fangio won his 1956 championship driving for which team?",
        ["Ferrari", "Maserati", "Mercedes", "Alfa Romeo"],
        0,
    ),
    (
        "Fangio clinched his 1954/1955 titles chiefly with which German marque?",
        ["Mercedes", "Auto Union", "BMW", "Porsche"],
        0,
    ),
    (
        "Fangio's final title, in 1957, came with which Italian team?",
        ["Maserati", "Ferrari", "Lancia", "Alfa Romeo"],
        0,
    ),
    (
        "Who won the very first World Championship race, the 1950 British GP?",
        ["Giuseppe Farina", "Juan Manuel Fangio", "Luigi Fagioli", "Alberto Ascari"],
        0,
    ),
    (
        "Which circuit hosted the first World Championship Grand Prix, in May 1950?",
        ["Silverstone", "Monaco", "Monza", "Reims"],
        0,
    ),
    (
        "The Indianapolis 500 counted toward the F1 championship from 1950 until which year?",
        ["1960", "1955", "1958", "1965"],
        0,
    ),
    (
        "The 1955 Le Mans disaster prompted which country to ban circuit racing, ending its GP?",
        ["Switzerland", "France", "Germany", "Spain"],
        0,
    ),
    (
        "The only World Championship 'Swiss GP' (1982) was actually held at which French circuit?",
        ["Dijon", "Le Castellet", "Reims", "Rouen"],
        0,
    ),
    (
        "Who won the 1958 title by a single point despite taking just one race win?",
        ["Mike Hawthorn", "Tony Brooks", "Peter Collins", "Roy Salvadori"],
        0,
    ),
    (
        "Which team pioneered the rear-engined revolution, taking the 1959 and 1960 titles?",
        ["Cooper", "Lotus", "BRM", "Vanwall"],
        0,
    ),
    ("BRM won its only Constructors' Championship in which year?", ["1962", "1959", "1965", "1958"], 0),
    ("Who won the 1962 title for BRM?", ["Graham Hill", "Jim Clark", "John Surtees", "Jack Brabham"], 0),
    ("Jim Clark won both his titles driving for which team?", ["Lotus", "BRM", "Cooper", "Brabham"], 0),
    (
        "Who won the 1964 championship, the only man to be champion on two and four wheels?",
        ["John Surtees", "Graham Hill", "Jim Clark", "Mike Hailwood"],
        0,
    ),
    (
        "Graham Hill lost the 1964 title by a single point to which driver?",
        ["John Surtees", "Jim Clark", "Lorenzo Bandini", "Dan Gurney"],
        0,
    ),
    (
        "The Cosworth DFV V8 debuted and won on its first outing in 1967, powering which car?",
        ["Lotus 49", "Brabham BT24", "McLaren M7A", "Matra MS10"],
        0,
    ),
    (
        "Who drove the Lotus 49 to victory on the Cosworth DFV's 1967 debut?",
        ["Jim Clark", "Graham Hill", "Jack Brabham", "Denny Hulme"],
        0,
    ),
    (
        "Denny Hulme won the 1967 World Championship for which team?",
        ["Brabham", "Lotus", "Ferrari", "Cooper"],
        0,
    ),
    (
        "Matra won the 1969 Constructors' title with which champion driver?",
        ["Jackie Stewart", "Jean-Pierre Beltoise", "Jacky Ickx", "Denny Hulme"],
        0,
    ),
    (
        "Jackie Stewart won his first title (1969) with which constructor?",
        ["Matra", "Tyrrell", "BRM", "March"],
        0,
    ),
    (
        "Emerson Fittipaldi won his first title (1972) with which team?",
        ["Lotus", "McLaren", "Brabham", "Tyrrell"],
        0,
    ),
    (
        "Fittipaldi won his second championship (1974) with which team?",
        ["McLaren", "Lotus", "Brabham", "Ferrari"],
        0,
    ),
    (
        "Which team's Lotus 79 made 'ground effect' dominant in 1978?",
        ["Lotus", "Brabham", "Tyrrell", "Ferrari"],
        0,
    ),
    (
        "Jean-Pierre Jabouille scored the first win for a turbo car at the 1979 French GP with which team?",
        ["Renault", "Ferrari", "Ligier", "Brabham"],
        0,
    ),
    (
        "Ronnie Peterson, a title contender, was fatally injured in a start crash at which 1978 race?",
        ["Italian GP", "German GP", "Dutch GP", "Austrian GP"],
        0,
    ),
    (
        "At which circuit did Tom Pryce die in 1977 after striking a marshal crossing the track?",
        ["Kyalami", "Zandvoort", "Interlagos", "Watkins Glen"],
        0,
    ),
    (
        "Which team built the 1978 'fan car' that won its only race before being withdrawn?",
        ["Brabham", "Lotus", "Tyrrell", "March"],
        0,
    ),
    (
        "Gordon Murray, designer of the Brabham fan car, later penned which legendary road car?",
        ["McLaren F1", "Ferrari F40", "Jaguar XJ220", "Bugatti EB110"],
        0,
    ),
    (
        "Niki Lauda won his first two titles (1975, 1977) with which team?",
        ["Ferrari", "McLaren", "Brabham", "BRM"],
        0,
    ),
    (
        "Niki Lauda won his third title (1984) with which team, beating team-mate Prost by half a point?",
        ["McLaren", "Ferrari", "Brabham", "Williams"],
        0,
    ),
    (
        "Elio de Angelis scored his first win by inches at the 1982 Austrian GP for which team?",
        ["Lotus", "Brabham", "Tyrrell", "Williams"],
        0,
    ),
    (
        "Riccardo Patrese took his first F1 win at the 1982 Monaco GP for which team?",
        ["Brabham", "Williams", "Arrows", "Alfa Romeo"],
        0,
    ),
    (
        "Gilles Villeneuve was killed in qualifying for the 1982 GP at which circuit?",
        ["Zolder", "Imola", "Zandvoort", "Hockenheim"],
        0,
    ),
    (
        "Which Ferrari driver led the 1982 title race before a career-ending crash at Hockenheim?",
        ["Didier Pironi", "Gilles Villeneuve", "René Arnoux", "Patrick Tambay"],
        0,
    ),
    (
        "Nelson Piquet won his 1981 and 1983 titles with which team?",
        ["Brabham", "Williams", "Lotus", "Benetton"],
        0,
    ),
    (
        "Nelson Piquet won his third and final title (1987) with which team?",
        ["Williams", "Brabham", "Lotus", "Benetton"],
        0,
    ),
    (
        "Which engine powered the dominant 1988 McLaren, and 15 of 16 wins, in its final turbo year?",
        ["Honda", "TAG-Porsche", "Renault", "Ford"],
        0,
    ),
    (
        "Ayrton Senna produced a famous rain-soaked charge to win the 1993 GP at which circuit?",
        ["Donington", "Silverstone", "Spa", "Estoril"],
        0,
    ),
    (
        "Which team ran the black-and-gold John Player Special livery in the '70s and '80s?",
        ["Lotus", "McLaren", "Brabham", "Shadow"],
        0,
    ),
    (
        "Which driver was disqualified from the 1994 British GP and later banned two races for ignoring a black flag?",
        ["Michael Schumacher", "Damon Hill", "Jean Alesi", "Gerhard Berger"],
        0,
    ),
    (
        "Roland Ratzenberger died in qualifying the day before Senna at which 1994 race?",
        ["San Marino GP", "Monaco GP", "Brazilian GP", "Pacific GP"],
        0,
    ),
    (
        "Senna's fatal 1994 crash occurred at which corner of Imola?",
        ["Tamburello", "Variante Alta", "Rivazza", "Acque Minerali"],
        0,
    ),
    (
        "Which team's active-suspension car dominated 1992 and 1993 before the tech was banned?",
        ["Williams", "McLaren", "Benetton", "Ferrari"],
        0,
    ),
    (
        "Nigel Mansell left F1 after his 1992 title and won the 1993 championship in which US series?",
        ["IndyCar (CART)", "NASCAR", "IMSA", "Trans-Am"],
        0,
    ),
    (
        "Damon Hill nearly won the 1997 Hungarian GP for which struggling team before a late failure?",
        ["Arrows", "Jordan", "Stewart", "Sauber"],
        0,
    ),
    (
        "Heinz-Harald Frentzen won the 1999 Italian GP for which team?",
        ["Jordan", "Williams", "Sauber", "Prost"],
        0,
    ),
    (
        "Eddie Irvine took the 1999 title fight to the finale driving for which team?",
        ["Ferrari", "Jaguar", "Jordan", "McLaren"],
        0,
    ),
    (
        "British American Racing (BAR) entered F1 in 1999 by buying which team's entry?",
        ["Tyrrell", "Arrows", "Minardi", "Ligier"],
        0,
    ),
    (
        "Rubens Barrichello scored his emotional first win at the wet 2000 GP at which circuit?",
        ["Hockenheim", "Silverstone", "Monza", "Spa"],
        0,
    ),
    (
        "Juan Pablo Montoya scored his maiden F1 win at which 2001 race?",
        ["Italian GP", "Brazilian GP", "German GP", "United States GP"],
        0,
    ),
    (
        "Toyota entered F1 as a works constructor in 2002 and left after which season, winless?",
        ["2009", "2008", "2010", "2006"],
        0,
    ),
    (
        "Fernando Alonso's maiden win (2003 Hungarian GP) made him the youngest winner at the time, for which team?",
        ["Renault", "Minardi", "McLaren", "Ferrari"],
        0,
    ),
    (
        "Kimi Räikkönen scored his first F1 win at which 2003 race?",
        ["Malaysian GP", "Australian GP", "Brazilian GP", "European GP"],
        0,
    ),
    (
        "Jarno Trulli scored his only F1 win at which 2004 race?",
        ["Monaco GP", "French GP", "Italian GP", "Belgian GP"],
        0,
    ),
    (
        "At the 2005 US GP, a tyre-safety row left how many cars to actually start?",
        ["6", "10", "14", "20"],
        0,
    ),
    (
        "Fernando Alonso won both his titles (2005, 2006) with which team?",
        ["Renault", "Ferrari", "McLaren", "Minardi"],
        0,
    ),
    (
        "Lewis Hamilton won his first title in 2008 by passing Timo Glock on the last lap of which race?",
        ["Brazilian GP", "Chinese GP", "Japanese GP", "Belgian GP"],
        0,
    ),
    ("By how many points did Hamilton win the 2008 championship?", ["1", "2", "4", "7"], 0),
    (
        "Sebastian Vettel scored his and Toro Rosso's first win at the wet 2008 GP at which circuit?",
        ["Monza", "Spa", "Shanghai", "Fuji"],
        0,
    ),
    (
        "Robert Kubica took his only F1 win at which 2008 race?",
        ["Canadian GP", "French GP", "Belgian GP", "Japanese GP"],
        0,
    ),
    (
        "BMW's works F1 win in 2008 came through its partnership with which team?",
        ["Sauber", "Williams", "Toyota", "Jordan"],
        0,
    ),
    (
        "Mark Webber scored his first F1 win at which 2009 race?",
        ["German GP", "British GP", "Brazilian GP", "Chinese GP"],
        0,
    ),
    (
        "Giancarlo Fisichella took a shock pole and nearly won the 2009 Belgian GP for which minnow team?",
        ["Force India", "Jordan", "Sauber", "Toro Rosso"],
        0,
    ),
    (
        "Which manufacturer supplied Brawn GP's title-winning 2009 engines?",
        ["Mercedes", "Honda", "Ferrari", "Renault"],
        0,
    ),
    (
        "Which engine maker powered Red Bull's four straight title cars from 2010 to 2013?",
        ["Renault", "Honda", "TAG Heuer", "Ferrari"],
        0,
    ),
    (
        "Michael Schumacher returned from retirement in 2010 to drive for which team?",
        ["Mercedes", "Ferrari", "Williams", "Renault"],
        0,
    ),
    (
        "Pastor Maldonado scored his shock win, Williams's last to date, at which 2012 race?",
        ["Spanish GP", "Monaco GP", "Italian GP", "Brazilian GP"],
        0,
    ),
    (
        "The V8 engine formula ran from 2006 until the end of which season?",
        ["2013", "2014", "2012", "2011"],
        0,
    ),
    ("Nico Rosberg beat Lewis Hamilton to the 2016 title by how many points?", ["5", "1", "2", "12"], 0),
    (
        "Which team gave a one-off drive to George Russell at the 2020 Sakhir GP?",
        ["Mercedes", "Williams", "Racing Point", "Aston Martin"],
        0,
    ),
    (
        "Sergio Pérez scored his maiden F1 win at the 2020 Sakhir GP for which team?",
        ["Racing Point", "Red Bull", "Force India", "BWT"],
        0,
    ),
    (
        "Which driver survived a fiery crash at the 2020 Bahrain GP, his car split by the barrier?",
        ["Romain Grosjean", "Kevin Magnussen", "Sergio Pérez", "Lance Stroll"],
        0,
    ),
    (
        "Pierre Gasly scored his maiden F1 win at which 2020 race?",
        ["Italian GP", "Belgian GP", "Turkish GP", "Portuguese GP"],
        0,
    ),
    (
        "The current Aston Martin team raced under which name in 2020?",
        ["Racing Point", "Force India", "Jordan", "Spyker"],
        0,
    ),
    (
        "Daniel Ricciardo ended McLaren's long win drought at which 2021 race?",
        ["Italian GP", "Belgian GP", "United States GP", "Dutch GP"],
        0,
    ),
    (
        "Esteban Ocon scored his maiden F1 win at which 2021 race?",
        ["Hungarian GP", "French GP", "Belgian GP", "Turkish GP"],
        0,
    ),
    (
        "Which driver finished runner-up to Verstappen in the 2022 championship?",
        ["Charles Leclerc", "Sergio Pérez", "Lewis Hamilton", "George Russell"],
        0,
    ),
    (
        "Carlos Sainz scored his maiden F1 win at which 2022 race?",
        ["British GP", "Belgian GP", "Italian GP", "United States GP"],
        0,
    ),
    (
        "George Russell scored his maiden F1 win at which 2022 race?",
        ["São Paulo GP", "Hungarian GP", "Mexico City GP", "Abu Dhabi GP"],
        0,
    ),
    (
        "Who scored the most points in a single season, the 2023 record of 575?",
        ["Max Verstappen", "Lewis Hamilton", "Sebastian Vettel", "Sergio Pérez"],
        0,
    ),
    ("The 2024 season featured a record how many Grands Prix?", ["24", "23", "22", "25"], 0),
    (
        "Lando Norris scored his maiden F1 win at which 2024 race?",
        ["Miami GP", "Dutch GP", "Singapore GP", "Emilia-Romagna GP"],
        0,
    ),
    (
        "Oscar Piastri scored his maiden F1 win at which 2024 race?",
        ["Hungarian GP", "Azerbaijan GP", "Belgian GP", "Qatar GP"],
        0,
    ),
    (
        "Which team won the 2024 Constructors' Championship?",
        ["McLaren", "Ferrari", "Red Bull", "Mercedes"],
        0,
    ),
    (
        "Lewis Hamilton announced in 2024 that he would join which team for 2025?",
        ["Ferrari", "Red Bull", "Aston Martin", "Audi"],
        0,
    ),
    (
        "Which teenager replaced Hamilton at Mercedes for 2025?",
        ["Andrea Kimi Antonelli", "Mick Schumacher", "Frederik Vesti", "Théo Pourchaire"],
        0,
    ),
    (
        "Audi will enter F1 in 2026 by taking over which existing team?",
        ["Sauber", "Williams", "Haas", "Alpine"],
        0,
    ),
    (
        "Which manufacturer joins F1 in 2026 to partner Red Bull's own powertrain?",
        ["Ford", "Porsche", "Audi", "General Motors"],
        0,
    ),
    (
        "From 2026, Aston Martin's works engines will be supplied by which manufacturer?",
        ["Honda", "Mercedes", "Renault", "Audi"],
        0,
    ),
    (
        "Who was the first Brazilian F1 World Champion (1972)?",
        ["Emerson Fittipaldi", "Nelson Piquet", "Ayrton Senna", "Carlos Pace"],
        0,
    ),
    (
        "Who was the first Australian F1 World Champion?",
        ["Jack Brabham", "Alan Jones", "Mark Webber", "Daniel Ricciardo"],
        0,
    ),
    (
        "Who was the first Austrian F1 World Champion (1970)?",
        ["Jochen Rindt", "Niki Lauda", "Gerhard Berger", "Jochen Mass"],
        0,
    ),
    (
        "Who was the first Spanish driver to win an F1 Grand Prix?",
        ["Fernando Alonso", "Pedro de la Rosa", "Carlos Sainz", "Jaime Alguersuari"],
        0,
    ),
    (
        "Who was the first Canadian to win an F1 Grand Prix?",
        ["Gilles Villeneuve", "Jacques Villeneuve", "Paul Tracy", "Lance Stroll"],
        0,
    ),
    (
        "Who was the first Japanese driver to stand on an F1 podium (1990)?",
        ["Aguri Suzuki", "Satoru Nakajima", "Ukyo Katayama", "Takuma Sato"],
        0,
    ),
    (
        "Who was the first Indian driver to start a Formula 1 race?",
        ["Narain Karthikeyan", "Karun Chandhok", "Jehan Daruvala", "Vijay Mallya"],
        0,
    ),
    (
        "Who became the first Chinese driver to race full-time in F1, debuting in 2022?",
        ["Zhou Guanyu", "Ma Qing Hua", "Cheng Congfu", "Ho-Pin Tung"],
        0,
    ),
    (
        "Who was the first Brazilian to win the drivers' title more than once?",
        ["Emerson Fittipaldi", "Nelson Piquet", "Ayrton Senna", "Rubens Barrichello"],
        0,
    ),
    (
        "Who was the first Mexican driver to win a Formula 1 Grand Prix?",
        ["Pedro Rodríguez", "Sergio Pérez", "Ricardo Rodríguez", "Esteban Gutiérrez"],
        0,
    ),
    (
        "Who was the first driver to reach 100 career Grand Prix wins?",
        ["Lewis Hamilton", "Michael Schumacher", "Sebastian Vettel", "Max Verstappen"],
        0,
    ),
    (
        "Michael Schumacher's career win tally, a record until Hamilton passed it, was how many?",
        ["91", "84", "95", "103"],
        0,
    ),
    (
        "Who holds the record for the most fastest laps in F1 history?",
        ["Michael Schumacher", "Lewis Hamilton", "Kimi Räikkönen", "Alain Prost"],
        0,
    ),
    (
        "Who holds the record for the most F1 starts without ever finishing on the podium?",
        ["Nico Hülkenberg", "Adrian Sutil", "Jos Verstappen", "Tiago Monteiro"],
        0,
    ),
    ("Sprint races were introduced to Formula 1 in which season?", ["2021", "2022", "2020", "2023"], 0),
    (
        "Which circuit hosted F1's first-ever Sprint race, in 2021?",
        ["Silverstone", "Monza", "Interlagos", "Red Bull Ring"],
        0,
    ),
    ("How many points was a Grand Prix win worth from 1991 to 2002?", ["10", "9", "8", "6"], 0),
    ("Before 1991, a Grand Prix win awarded how many points?", ["9", "10", "8", "6"], 0),
    (
        "F1 switched to the current 25-points-for-a-win system in which year?",
        ["2010", "2009", "2014", "2003"],
        0,
    ),
    (
        "When the fastest-lap bonus point existed (2019-2024), a driver had to finish where to claim it?",
        ["In the top ten", "On the podium", "In the top five", "Anywhere, if classified"],
        0,
    ),
    (
        "What does 'DRS' stand for?",
        [
            "Drag Reduction System",
            "Downforce Reduction System",
            "Dynamic Rear Slot",
            "Direct Response Steering",
        ],
        0,
    ),
    (
        "What does 'ERS' stand for in modern power units?",
        [
            "Energy Recovery System",
            "Engine Recovery System",
            "Electric Racing System",
            "Exhaust Recovery System",
        ],
        0,
    ),
    (
        "The MGU-H recovers energy from which component?",
        ["The turbocharger", "The brakes", "The suspension", "The gearbox"],
        0,
    ),
    (
        "The MGU-K recovers energy primarily from what?",
        ["Braking", "The turbo", "The exhaust", "The engine block"],
        0,
    ),
    ("The MGU-H will be dropped under which season's new engine rules?", ["2026", "2025", "2027", "2030"], 0),
    ("The V6 turbo-hybrid power unit formula began in which season?", ["2014", "2013", "2016", "2009"], 0),
    (
        "F1 cars used screaming V10 engines until the end of which season?",
        ["2005", "2006", "2004", "2000"],
        0,
    ),
    ("F1's budget cost cap came into force in which year?", ["2021", "2020", "2022", "2019"], 0),
    (
        "Which team was penalised for breaching the 2021 cost cap?",
        ["Red Bull", "Mercedes", "Ferrari", "Aston Martin"],
        0,
    ),
    (
        "Grooved dry tyres were used from 1998 until slicks returned in which year?",
        ["2009", "2006", "2011", "2008"],
        0,
    ),
    ("Traction control was banned again from which season?", ["2008", "2006", "2009", "2010"], 0),
    (
        "The 2022 regulations increased the wheel-rim diameter to what size?",
        ["18 inches", "13 inches", "15 inches", "20 inches"],
        0,
    ),
    (
        "Before 2022, F1 used wheel rims of what diameter?",
        ["13 inches", "15 inches", "16 inches", "18 inches"],
        0,
    ),
    (
        "A standard Grand Prix runs the number of laps needed to exceed roughly what distance?",
        ["305 km", "260 km", "200 km", "400 km"],
        0,
    ),
    (
        "Which race is permitted a shorter distance of about 260 km?",
        ["Monaco", "Singapore", "Monza", "Spa"],
        0,
    ),
    (
        "What does a blue flag shown to a driver mean?",
        ["Let faster lapping cars past", "Track is slippery", "Race is stopped", "You are disqualified"],
        0,
    ),
    (
        "A green flag signals what?",
        [
            "The track is clear / hazard passed",
            "Start your engines",
            "One lap remaining",
            "Return to the pits",
        ],
        0,
    ),
    (
        "A yellow-and-red striped flag warns of what?",
        [
            "A slippery surface such as oil or debris",
            "An ambulance on track",
            "The final lap",
            "Safety car deployed",
        ],
        0,
    ),
    (
        "The Virtual Safety Car was introduced after which driver's fatal accident?",
        ["Jules Bianchi", "Ayrton Senna", "Roland Ratzenberger", "María de Villota"],
        0,
    ),
    (
        "At which 2014 race was Jules Bianchi critically injured?",
        ["Japanese GP", "Malaysian GP", "Singapore GP", "Russian GP"],
        0,
    ),
    (
        "What is the 'delta time' drivers must respect under a Safety Car or VSC?",
        [
            "A minimum lap time they must not beat",
            "A maximum pit-stop time",
            "A fuel-saving target",
            "A tyre-temperature window",
        ],
        0,
    ),
    (
        "What does 'box, box' over team radio instruct a driver to do?",
        ["Come into the pits", "Defend position", "Save fuel", "Attack the car ahead"],
        0,
    ),
    (
        "The 'formation lap' is also commonly called what?",
        ["The parade lap", "The out lap", "The cool-down lap", "The reconnaissance lap"],
        0,
    ),
    (
        "A 'stop-go' penalty requires a driver to do what?",
        [
            "Stop in his pit box for a set time and take on no service",
            "Serve five seconds at his next stop",
            "Drop one grid place",
            "Complete a drive-through only",
        ],
        0,
    ),
    (
        "What is 'parc fermé'?",
        [
            "Rules restricting car changes between qualifying and the race",
            "A pit-lane service area",
            "A type of wet tyre",
            "A grid-penalty zone",
        ],
        0,
    ),
    (
        "The 'Eau Rouge / Raidillon' complex is at which circuit?",
        ["Spa-Francorchamps", "Monaco", "Suzuka", "Silverstone"],
        0,
    ),
    (
        "The long 'Kemmel Straight' immediately follows which corner?",
        ["Eau Rouge/Raidillon", "Pouhon", "Blanchimont", "La Source"],
        0,
    ),
    ("The 'Tamburello' corner is at which circuit?", ["Imola", "Monza", "Mugello", "Vallelunga"], 0),
    ("The 'Ascari' chicane is found at which circuit?", ["Monza", "Imola", "Mugello", "Vallelunga"], 0),
    ("The 'Rascasse' hairpin is a corner at which circuit?", ["Monaco", "Singapore", "Baku", "Montreal"], 0),
    (
        "The 'Casino Square' and 'Mirabeau' are corners at which circuit?",
        ["Monaco", "Baku", "Singapore", "Montreal"],
        0,
    ),
    ("The infamous 'Wall of Champions' is at which circuit?", ["Montreal", "Baku", "Monaco", "Singapore"], 0),
    ("The 'Degner' curves are at which circuit?", ["Suzuka", "Fuji", "Sepang", "Shanghai"], 0),
    ("The 'Senna S' opens which circuit's lap?", ["Interlagos", "Estoril", "Jerez", "Buenos Aires"], 0),
    (
        "'Copse', 'Maggotts' and 'Stowe' are corners at which circuit?",
        ["Silverstone", "Brands Hatch", "Donington", "Snetterton"],
        0,
    ),
    (
        "The old Nürburgring 'Nordschleife' loop is nicknamed what?",
        ["The Green Hell", "The Black Forest", "The Long Circuit", "The Ring of Fire"],
        0,
    ),
    (
        "Istanbul Park's famous multi-apex 'Turn 8' is on which circuit?",
        ["Istanbul Park", "Sochi", "Baku", "Yas Marina"],
        0,
    ),
    (
        "The Dutch GP returned in 2021 with banked corners at which circuit?",
        ["Zandvoort", "Assen", "Nürburgring", "Spa"],
        0,
    ),
    ("Qatar first hosted F1 in 2021 at which circuit?", ["Losail", "Yas Marina", "Sakhir", "Jeddah"], 0),
    (
        "Imola's official name honours which father and son?",
        [
            "Enzo and Dino Ferrari",
            "Alberto and Antonio Ascari",
            "Graham and Damon Hill",
            "Gilles and Jacques Villeneuve",
        ],
        0,
    ),
    (
        "The Nürburgring hosted a 2020 race under which name due to the pandemic?",
        ["Eifel Grand Prix", "German Grand Prix", "European Grand Prix", "Rhine Grand Prix"],
        0,
    ),
    (
        "Which circuit hosted the one-off 2020 'Tuscan Grand Prix'?",
        ["Mugello", "Imola", "Monza", "Vallelunga"],
        0,
    ),
    ("Portugal returned to F1 in 2020 at which circuit?", ["Portimão", "Estoril", "Porto", "Jarama"], 0),
    ("The Miami Grand Prix debuted in which year?", ["2022", "2021", "2023", "2020"], 0),
    ("Saudi Arabia's F1 street race is held in which city?", ["Jeddah", "Riyadh", "Mecca", "Dammam"], 0),
    (
        "Who founded Lotus and pioneered the monocoque and ground effect?",
        ["Colin Chapman", "Enzo Ferrari", "Ken Tyrrell", "John Cooper"],
        0,
    ),
    (
        "Designer Adrian Newey joined which team in 2006 to build title-winners?",
        ["Red Bull", "Ferrari", "Mercedes", "Aston Martin"],
        0,
    ),
    (
        "Which team principal led Ferrari to six straight constructors' titles (1999-2004)?",
        ["Jean Todt", "Luca di Montezemolo", "Stefano Domenicali", "Mattia Binotto"],
        0,
    ),
    (
        "Which flamboyant boss ran Benetton's 1994-95 and Renault's 2005-06 title campaigns?",
        ["Flavio Briatore", "Tom Walkinshaw", "Pat Symonds", "Jean Todt"],
        0,
    ),
    (
        "Frank Williams's team took its first Constructors' Championship in which year?",
        ["1980", "1979", "1982", "1986"],
        0,
    ),
    (
        "The current Alpine team raced as which marque from 2002-2011 and 2016-2020?",
        ["Renault", "Lotus", "Benetton", "Toleman"],
        0,
    ),
    (
        "Which US-based outfit joined the grid as a full constructor in 2016?",
        ["Haas", "Andretti", "Penske", "Ganassi"],
        0,
    ),
    (
        "Which team ran the blue-and-white Rothmans livery through its mid-'90s titles?",
        ["Williams", "McLaren", "Benetton", "Jordan"],
        0,
    ),
    (
        "Mercedes' works cars revived which historic nickname?",
        ["Silver Arrows", "Prancing Horse", "Red Devils", "Flying Finns"],
        0,
    ),
    (
        "Which team ran the red-and-white Marlboro livery to the 1988 and 1989 titles?",
        ["McLaren", "Ferrari", "Williams", "Lotus"],
        0,
    ),
    (
        "Which driver took Stewart Grand Prix's only win, at the 1999 European GP?",
        ["Johnny Herbert", "Rubens Barrichello", "Jos Verstappen", "Jean Alesi"],
        0,
    ),
    (
        "Thierry Boutsen scored his first F1 win at the rain-hit 1989 GP at which circuit?",
        ["Montreal", "Adelaide", "Budapest", "Spa"],
        0,
    ),
    (
        "Which backmarker did Olivier Panis win the 1996 Monaco GP for?",
        ["Ligier", "Minardi", "Arrows", "Tyrrell"],
        0,
    ),
    (
        "Jean Alesi's long-awaited only win came at the 1995 GP at which circuit?",
        ["Montreal", "Monza", "Nürburgring", "Estoril"],
        0,
    ),
    (
        "Which team scored its first win at the 1998 Belgian GP via Damon Hill?",
        ["Jordan", "Stewart", "Sauber", "Prost"],
        0,
    ),
    (
        "Peter Gethin won the closest finish in F1 history (0.01s) at the 1971 GP at which circuit?",
        ["Monza", "Silverstone", "Zandvoort", "Watkins Glen"],
        0,
    ),
    (
        "Who won the first F1 race held in Jeddah, the 2021 Saudi Arabian GP?",
        ["Lewis Hamilton", "Max Verstappen", "Valtteri Bottas", "Sergio Pérez"],
        0,
    ),
    (
        "Which driver put a Toro Rosso on pole at the wet 2008 Italian GP?",
        ["Sebastian Vettel", "Sébastien Bourdais", "Jaime Alguersuari", "Daniil Kvyat"],
        0,
    ),
    (
        "Ralf Schumacher scored his first F1 win in 2001 for which team?",
        ["Williams", "Jordan", "Toyota", "Sauber"],
        0,
    ),
    (
        "Which team did Sergio Pérez join for 2021 to partner Max Verstappen?",
        ["Red Bull", "Racing Point", "Aston Martin", "Alfa Romeo"],
        0,
    ),
    (
        "Who holds the record for most consecutive race wins, set in 2023 (ten)?",
        ["Max Verstappen", "Sebastian Vettel", "Michael Schumacher", "Alberto Ascari"],
        0,
    ),
    (
        "Zhou Guanyu survived a dramatic startline flip at which 2022 race?",
        ["British GP", "Hungarian GP", "Austrian GP", "Italian GP"],
        0,
    ),
    ("How many different winners were there in the wide-open 1982 season?", ["11", "7", "9", "5"], 0),
    (
        "Which driver ended a long drought at the 2024 British GP, his first win since 2021?",
        ["Lewis Hamilton", "George Russell", "Lando Norris", "Oscar Piastri"],
        0,
    ),
    (
        "Phil Hill clinched the 1961 title at Monza, a race marred by the death of which team-mate?",
        ["Wolfgang von Trips", "Ricardo Rodríguez", "Giancarlo Baghetti", "Richie Ginther"],
        0,
    ),
    (
        "Which driver, later a team owner, won the 1978 title after his team-mate's fatal crash?",
        ["Mario Andretti", "Ronnie Peterson", "Jody Scheckter", "Carlos Reutemann"],
        0,
    ),
    (
        "The 'sharknose' nickname referred to the front of which team's 1961 car?",
        ["Ferrari", "Lotus", "BRM", "Cooper"],
        0,
    ),
    (
        "Who took McLaren's first Grand Prix win as a constructor, at the 1968 Belgian GP?",
        ["Bruce McLaren", "Denny Hulme", "Dan Gurney", "Peter Revson"],
        0,
    ),
    (
        "Which team did Bruce McLaren drive for before founding his own constructor?",
        ["Cooper", "Lotus", "BRM", "Brabham"],
        0,
    ),
    (
        "Jochen Rindt's posthumous 1970 title was won with which team?",
        ["Lotus", "Brabham", "March", "BRM"],
        0,
    ),
    (
        "Who was Red Bull's engine-branding partner (from 2016-2018) after the Renault fallout?",
        ["TAG Heuer", "Honda", "Infiniti", "Aston Martin"],
        0,
    ),
    (
        "Honda officially left F1 at the end of which year, though its tech stayed with Red Bull?",
        ["2021", "2020", "2022", "2019"],
        0,
    ),
    (
        "Which team did Sebastian Vettel move to for the 2015 season?",
        ["Ferrari", "Mercedes", "Williams", "McLaren"],
        0,
    ),
    (
        "Daniel Ricciardo won three races in which breakout season, splitting the Mercedes pair?",
        ["2014", "2013", "2016", "2018"],
        0,
    ),
    (
        "Carlos Sainz scored the only non-Red Bull win of 2023 at which race?",
        ["Singapore GP", "Italian GP", "Las Vegas GP", "Mexico City GP"],
        0,
    ),
    (
        "Valtteri Bottas moved from Williams to Mercedes for which season?",
        ["2017", "2016", "2018", "2019"],
        0,
    ),
    (
        "Charles Leclerc scored his maiden F1 win at which 2019 race?",
        ["Belgian GP", "Italian GP", "Bahrain GP", "Austrian GP"],
        0,
    ),
    (
        "Charles Leclerc delivered Ferrari an emotional home win at which circuit in 2019?",
        ["Monza", "Imola", "Mugello", "Fiorano"],
        0,
    ),
    (
        "Which team did Fernando Alonso return to F1 with in 2021 after two years away?",
        ["Alpine", "McLaren", "Aston Martin", "Renault"],
        0,
    ),
    (
        "Fernando Alonso ended a long podium drought by joining which team in 2023?",
        ["Aston Martin", "Alpine", "McLaren", "Williams"],
        0,
    ),
    (
        "Who is the only driver to win the Indianapolis 500, the Monaco GP and Le Mans (the Triple Crown)?",
        ["Graham Hill", "Mario Andretti", "Jackie Stewart", "Juan Pablo Montoya"],
        0,
    ),
    (
        "Which father-and-son pair are the only one to each win the F1 drivers' title?",
        ["Graham & Damon Hill", "Keke & Nico Rosberg", "Jos & Max Verstappen", "Gilles & Jacques Villeneuve"],
        0,
    ),
    (
        "Which other father-son pair are both F1 World Champions, the champions of 1982 and 2016?",
        ["Keke & Nico Rosberg", "Graham & Damon Hill", "Jos & Max Verstappen", "Jack & Gary Brabham"],
        0,
    ),
    (
        "Which driver won the 1980 title for Williams, its first drivers' crown?",
        ["Alan Jones", "Carlos Reutemann", "Nelson Piquet", "Jody Scheckter"],
        0,
    ),
    (
        "Jody Scheckter won the 1979 title for Ferrari alongside which charismatic team-mate?",
        ["Gilles Villeneuve", "Carlos Reutemann", "Patrick Tambay", "René Arnoux"],
        0,
    ),
    (
        "Who set the previous single-season wins record of 13, shared by Schumacher (2004) and which driver in 2011?",
        ["Sebastian Vettel", "Fernando Alonso", "Mark Webber", "Jenson Button"],
        0,
    ),
    (
        "Which team did Kimi Räikkönen win his sole title with, in 2007?",
        ["Ferrari", "McLaren", "Lotus", "Sauber"],
        0,
    ),
    ("By how many points did Räikkönen win the 2007 title over the two McLarens?", ["1", "2", "5", "7"], 0),
    (
        "Which driver won the first Formula 1 night race, the 2008 Singapore GP?",
        ["Fernando Alonso", "Felipe Massa", "Lewis Hamilton", "Nico Rosberg"],
        0,
    ),
    (
        "The 2008 Singapore win was later tainted by which scandal?",
        ["Crashgate", "Spygate", "Liegate", "Stepneygate"],
        0,
    ),
    (
        "Which team ordered the deliberate crash at the heart of 'Crashgate'?",
        ["Renault", "McLaren", "Ferrari", "Toyota"],
        0,
    ),
    (
        "Nelson Piquet Jr. was the driver told to crash in the 'Crashgate' affair at which race?",
        ["2008 Singapore GP", "2008 Malaysian GP", "2009 Singapore GP", "2008 Japanese GP"],
        0,
    ),
    (
        "Which team was fined a record sum and given a suspended ban over 2007's 'Spygate'?",
        ["McLaren", "Ferrari", "Renault", "Williams"],
        0,
    ),
    (
        "Which champion is nicknamed 'The Iceman'?",
        ["Kimi Räikkönen", "Mika Häkkinen", "Valtteri Bottas", "Keke Rosberg"],
        0,
    ),
    (
        "Which driver was nicknamed 'Il Leone' (The Lion) by the Italian fans?",
        ["Nigel Mansell", "Alain Prost", "Gilles Villeneuve", "Riccardo Patrese"],
        0,
    ),
    (
        "Max Verstappen won his fourth consecutive drivers' title in which year?",
        ["2024", "2023", "2025", "2022"],
        0,
    ),
    (
        "Max Verstappen clinched his third title at the sprint of which 2023 race?",
        ["Qatar GP", "United States GP", "Japanese GP", "Mexico City GP"],
        0,
    ),
    (
        "George Russell gave Mercedes its first win of 2024 at which race?",
        ["Austrian GP", "British GP", "Belgian GP", "Las Vegas GP"],
        0,
    ),
    (
        "Nyck de Vries scored points on his surprise F1 debut at the 2022 Italian GP, standing in for which team?",
        ["Williams", "Aston Martin", "Alpine", "AlphaTauri"],
        0,
    ),
    (
        "Oscar Piastri signed for which team in 2022 after a contract row with Alpine?",
        ["McLaren", "Williams", "Aston Martin", "Mercedes"],
        0,
    ),
    (
        "Carlos Sainz signed with which team for 2025 after Ferrari chose Hamilton?",
        ["Williams", "Audi", "Alpine", "Red Bull"],
        0,
    ),
    (
        "Esteban Ocon left Alpine to join which team for 2025?",
        ["Haas", "Williams", "Sauber", "Aston Martin"],
        0,
    ),
    (
        "Nico Hülkenberg signed with which team for 2025 as it transitions to Audi?",
        ["Sauber", "Haas", "Williams", "Alpine"],
        0,
    ),
    (
        "Which team did Daniel Ricciardo leave McLaren for, returning to the grid in 2023 mid-season?",
        ["AlphaTauri", "Williams", "Haas", "Alpine"],
        0,
    ),
    (
        "Guanyu Zhou raced for which team during his 2022-2024 F1 stint?",
        ["Alfa Romeo / Sauber", "Haas", "Williams", "AlphaTauri"],
        0,
    ),
    (
        "Which team rebranded from AlphaTauri to 'RB' (later Racing Bulls) for 2024?",
        ["Toro Rosso lineage (RB)", "Force India lineage", "Minardi-only", "Jaguar lineage"],
        0,
    ),
    (
        "Fernando Alonso passed 400 Grand Prix entries, a first in F1, during which season?",
        ["2024", "2023", "2025", "2022"],
        0,
    ),
]


class _AnswerButton(discord.ui.Button):
    def __init__(self, label: str, index: int):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.on_answer(interaction, self.index)


class TriviaView(discord.ui.View):
    def __init__(
        self,
        cog,
        player_id: int,
        options: List[str],
        correct: int,
        correct_label: str,
        reward: int,
        timeout: int,
        title: str,
        guild_id: int,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.player_id = player_id
        self.correct = correct
        self.correct_label = correct_label
        self.reward = reward
        self.title = title
        self.guild_id = guild_id
        self.generation = reset_generation(cog.user(guild_id, player_id))
        self.started_at = _now()
        self.answered = False
        self.message: Optional[discord.Message] = None
        for i, opt in enumerate(options):
            self.add_item(_AnswerButton(opt[:80], i))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await ui.respond(
                interaction,
                embed=ui.error_embed("This isn't your question — run `/trivia` for your own."),
                ephemeral=True,
            )
            return False
        return True

    async def on_answer(self, interaction: discord.Interaction, index: int) -> None:
        if self.answered:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return
        self.answered = True
        correct = index == self.correct
        for item in self.children:
            item.disabled = True
            if getattr(item, "index", None) == self.correct:
                item.style = discord.ButtonStyle.success
            elif getattr(item, "index", None) == index:
                item.style = discord.ButtonStyle.danger
        await interaction.response.defer()
        if reset_generation(self.cog.user(self.guild_id, self.player_id)) != self.generation:
            await interaction.edit_original_response(
                embed=ui.warn_embed("This question ended when the account was reset. Start a new question."),
                view=self,
            )
            self.stop()
            return
        cfg = self.cog.cfg(interaction.guild_id)
        if correct:
            result = await self.cog.settle_answer(
                interaction.guild_id,
                self.player_id,
                True,
                (_now() - self.started_at).total_seconds(),
                self.reward,
            )
            desc = f"✅ **Correct!** You earned {self.cog.money(cfg, result['reward'])}.\nStreak: **{result['streak']}** · speed bonus: **{self.cog.money(cfg, result['speed'])}**"
            if result.get("milestone"):
                desc += f"\n🏆 Daily 5-answer bonus: **{self.cog.money(cfg, result['milestone'])}**"
            if result.get("scholar"):
                desc += "\n📚 **Trivia Scholar unlocked!** The title is now in `/titles`."
            desc += f"\nBalance: {self.cog.money(cfg, result['balance'])}"
            color = ui.COLOR_OK
        else:
            result = await self.cog.settle_answer(
                interaction.guild_id,
                self.player_id,
                False,
                (_now() - self.started_at).total_seconds(),
                self.reward,
            )
            protected = bool(result.get("protected"))
            desc = f"❌ **Wrong!** The answer was **{self.correct_label}**."
            desc += (
                "\n📚 Scholar 21 protected your streak today."
                if protected
                else "\nYour trivia streak has reset."
            )
            color = ui.COLOR_BAD
        await interaction.edit_original_response(
            embed=ui.base_embed(title=self.title, description=desc, color=color), view=self
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.answered or self.message is None:
            return
        for item in self.children:
            item.disabled = True
        self.answered = True
        if reset_generation(self.cog.user(self.guild_id, self.player_id)) == self.generation:
            await self.cog.settle_answer(
                self.guild_id, self.player_id, False, (_now() - self.started_at).total_seconds(), self.reward
            )
        try:
            await self.message.edit(
                embed=ui.warn_embed(f"⏱️ Time's up! The answer was **{self.correct_label}**."), view=self
            )
        except discord.HTTPException:
            pass


class Trivia(commands.Cog):
    """Dragon Ball trivia for donuts."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        starting = int(self.cfg(guild_id).get("economy.starting_balance", 100))
        return self.econ.user(guild_id, user_id, starting)

    def money(self, cfg, amount: int) -> str:
        emoji = str(cfg.get("economy.currency_emoji", "🍩"))
        name = str(cfg.get("economy.currency_name", "donuts"))
        return f"{emoji} **{ui.format_donuts(amount)}** {name}"

    async def persist(self, guild_id: int) -> None:
        await self.econ.save(guild_id)

    async def _guard(self, interaction: discord.Interaction):
        await ui.defer_response(interaction)
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Trivia only works in a server."), ephemeral=True
            )
            return None
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is turned off here."), ephemeral=True
            )
            return None
        return cfg

    async def settle_answer(
        self, guild_id: int, user_id: int, correct: bool, elapsed: float, base: int
    ) -> Dict[str, Any]:
        """Settle one answer, including streak, speed and daily milestones."""
        u = self.user(guild_id, user_id)
        stats = u.setdefault("trivia_stats", {})
        if not isinstance(stats, dict):
            stats = u["trivia_stats"] = {}
        today = _now().date().isoformat()
        if stats.get("day") != today:
            stats["day"] = today
            stats["daily_correct"] = 0
            stats["daily_bonus_claimed"] = False
        stats["answered"] = int(stats.get("answered", 0)) + 1
        if not correct:
            stats["wrong"] = int(stats.get("wrong", 0)) + 1
            protected = False
            if plushie_perk(u, "trivia_guard") and stats.get("guard_day") != today:
                stats["guard_day"] = today
                protected = True
            else:
                stats["streak"] = 0
            await self.persist(guild_id)
            return {"protected": protected, "streak": int(stats.get("streak", 0))}
        streak = int(stats.get("streak", 0)) + 1
        stats["streak"] = streak
        stats["best_streak"] = max(streak, int(stats.get("best_streak", 0)))
        stats["correct"] = int(stats.get("correct", 0)) + 1
        stats["daily_correct"] = int(stats.get("daily_correct", 0)) + 1
        streak_bonus = min(100, max(0, streak - 1) * 10)
        allowed = max(
            1, int(self.cfg(guild_id).get("economy.trivia_seconds", 25)) + plushie_perk(u, "trivia")
        )
        speed_max = max(0, int(self.cfg(guild_id).get("economy.trivia_speed_bonus_max", 550000)))
        speed = max(0, int(speed_max * (1 - min(float(elapsed), allowed) / allowed)))
        reward = int(base) + int(base) * streak_bonus // 100 + speed
        milestone = 0
        if stats["daily_correct"] >= 5 and (not stats.get("daily_bonus_claimed")):
            milestone = max(0, int(self.cfg(guild_id).get("economy.trivia_daily_milestone", 5500000)))
            stats["daily_bonus_claimed"] = True
        u["donuts"] = int(u.get("donuts", 0)) + reward + milestone
        scholar = False
        if streak >= 10 and (not u.setdefault("titles", {}).get("scholar")):
            u["titles"]["scholar"] = 1
            badges.grant(u, "trivia_scholar")
            scholar = True
        await self.persist(guild_id)
        await self.bot.ledger.record(
            guild_id,
            user_id,
            reward + milestone,
            "trivia",
            after=int(u.get("donuts", 0)) + int(u.get("bank", 0)),
        )
        return {
            "reward": reward,
            "speed": speed,
            "milestone": milestone,
            "streak": streak,
            "balance": int(u.get("donuts", 0)),
            "scholar": scholar,
        }

    @app_commands.command(name="trivia", description="Answer a Dragon Ball or F1 trivia question for donuts.")
    async def trivia(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        cd = int(cfg.get("economy.trivia_cooldown_minutes", 120)) * 60
        last = _parse(u.get("trivia_at"))
        if last is not None and (_now() - last).total_seconds() < cd:
            ready = int(last.timestamp() + cd)
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Rest your brain, scholar. Next question <t:{ready}:R>."),
                ephemeral=True,
            )
            return
        source, title = (F1_QUESTIONS, "🏎️ F1 Trivia")
        valid = range(len(source))
        queue = [i for i in u.get("trivia_queue", []) or [] if isinstance(i, int) and i in valid]
        if not queue:
            queue = list(valid)
            random.shuffle(queue)
            recent = set(u.get("trivia_recent", [])[-15:])
            queue.sort(key=lambda i: i in recent, reverse=True)
        pick = queue.pop()
        u["trivia_queue"] = queue
        served = [i for i in u.get("trivia_recent", []) or [] if isinstance(i, int)]
        served.append(pick)
        u["trivia_recent"] = served[-15:]
        u["trivia_at"] = _now().isoformat()
        await self.persist(interaction.guild_id)
        question = source[pick]
        q_text, q_opts, q_ans = question
        order = list(enumerate(q_opts))
        random.shuffle(order)
        labels = [text for _, text in order]
        correct = next((i for i, (oi, _) in enumerate(order) if oi == q_ans))
        reward = int(cfg.get("economy.trivia_reward", 1100000))
        secs = int(cfg.get("economy.trivia_seconds", 25)) + plushie_perk(u, "trivia")
        view = TriviaView(
            self,
            interaction.user.id,
            labels,
            correct,
            labels[correct],
            reward,
            secs,
            title,
            interaction.guild_id,
        )
        stats = u.get("trivia_stats", {}) if isinstance(u.get("trivia_stats"), dict) else {}
        embed = ui.base_embed(
            title=title,
            description=f"**{q_text}**\n\n*{secs}s to answer · {self.money(cfg, reward)} base + streak and speed bonuses · current streak {int(stats.get('streak', 0))}.*",
            color=cfg.color,
        )
        await ui.respond(interaction, embed=embed, view=view)
        view.message = await ui.response_message(interaction)

    @app_commands.command(name="triviastats", description="Privately view your trivia record and streak.")
    async def trivia_stats(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        stats = u.get("trivia_stats", {}) if isinstance(u.get("trivia_stats"), dict) else {}
        answered = int(stats.get("answered", 0))
        correct = int(stats.get("correct", 0))
        accuracy = correct * 100 / answered if answered else 0
        milestone = int(cfg.get("economy.trivia_daily_milestone", 5500000))
        embed = ui.base_embed(
            title=f"📚 {interaction.user.display_name}'s Trivia Record",
            description=f"Answered **{answered:,}** · correct **{correct:,}** · accuracy **{accuracy:.1f}%**\nCurrent streak **{int(stats.get('streak', 0))}** · best **{int(stats.get('best_streak', 0))}**\nToday **{int(stats.get('daily_correct', 0))}/5** correct toward the daily {self.money(cfg, milestone)} bonus",
            color=cfg.color,
        )
        await ui.respond(interaction, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Trivia(bot))
