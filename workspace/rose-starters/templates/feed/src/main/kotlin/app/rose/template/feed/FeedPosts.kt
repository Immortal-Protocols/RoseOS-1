package app.rose.template.feed

internal data class FeedPost(
    val id: Int,
    val topic: String,
    val title: String,
    val summary: String,
    val byline: String,
    val body: String,
)

internal val samplePosts = listOf(
    FeedPost(
        id = 1,
        topic = "Waterfront",
        title = "The harbor before sunrise",
        summary = "On the first ferry, the city comes into view one lit window at a time.",
        byline = "Maya Chen · Apr 18",
        body = "The harbor is mostly sound from the upper deck. A loose line knocks against a mast, then the ramp folds " +
            "away with a low rattle. By the time the ferry turns, the fish market has lit its awning.\n\n" +
            "The first crossing takes twelve minutes. Sit on the inland side to see the windows come on above the old quay.",
    ),
    FeedPost(
        id = 2,
        topic = "Food",
        title = "A bakery with one good table",
        summary = "The best seat at Cedar Bakery is halfway between the oven and the street.",
        byline = "Jon Bell · Apr 17",
        body = "At the long table by the window, regulars leave room for the morning pastry trays. Order at the counter " +
            "and take whichever chair happens to be free.\n\n" +
            "Ask when the next tray of cardamom buns comes out. When there is a wait, a coffee buys you time to watch " +
            "the street wake up.",
    ),
    FeedPost(
        id = 3,
        topic = "Gardens",
        title = "The seed library upstairs",
        summary = "A drawer of labeled envelopes makes the case for growing something from scratch.",
        byline = "Maya Chen · Apr 16",
        body = "The reading room keeps its seed exchange in a repurposed card catalog. Each envelope says who saved the " +
            "seeds and whether they did well in a pot or in the ground.\n\n" +
            "The volunteers suggest starting with one or two envelopes. Bring back seeds at the end of the season if " +
            "your plants produce enough to share.",
    ),
    FeedPost(
        id = 4,
        topic = "Street life",
        title = "Under the viaduct",
        summary = "A disused stretch of pavement now hosts a Saturday repair stand.",
        byline = "Inez Park · Apr 15",
        body = "The repair stand has two workbenches, a pump and a bowl of spare bolts. On Saturday mornings, neighbors " +
            "roll in bikes and leave with tires patched or brakes adjusted.\n\n" +
            "Bring a bike with a problem you can describe. The volunteers usually teach you the fix so you can do it " +
            "again on your own.",
    ),
    FeedPost(
        id = 5,
        topic = "Music",
        title = "The record shop listening hour",
        summary = "At noon on Thursdays, the shop turns over a whole album before taking requests.",
        byline = "Jon Bell · Apr 14",
        body = "The turntable sits behind the counter, but the needle drop is audible from the back shelves. A " +
            "handwritten card records the title so listeners can find the sleeve after the album finishes.\n\n" +
            "There are a few stools by the speakers. If they are taken, the jazz bins are usually a good place to " +
            "stand and listen.",
    ),
    FeedPost(
        id = 6,
        topic = "Walks",
        title = "The long way home",
        summary = "An extra block reveals a footbridge, a pocket orchard and a way off the main road.",
        byline = "Inez Park · Apr 13",
        body = "Take the footbridge behind the station and follow the canal past the brick pump house. A path through " +
            "the apple trees leads back to the main road.\n\n" +
            "The orchard gate closes at dusk. From the field, you can see the last trains cross the bridge before " +
            "they curve into the station.",
    ),
    FeedPost(
        id = 7,
        topic = "Making",
        title = "A bench for the whole block",
        summary = "Neighbors turned some reclaimed boards into a place to stop on the way uphill.",
        byline = "Maya Chen · Apr 12",
        body = "The wood came from a dismantled school stage. Volunteers sanded one side smooth and left the old paint " +
            "on the underside, where you can still make out the tape marks.\n\n" +
            "The finished bench faces west. It gets the last bit of evening light long after the rest of the street " +
            "falls into shadow.",
    ),
    FeedPost(
        id = 8,
        topic = "Waterfront",
        title = "A map of the tide line",
        summary = "At low tide, the marks on the harbor steps tell a small story about the day.",
        byline = "Inez Park · Apr 11",
        body = "The steps are dark below the waterline and pale at the top. In between, the drying stone makes a chart " +
            "you can read without a watch.\n\n" +
            "Look from the landing. A few steps disappear as the water returns, and the lower handrail is slippery " +
            "even on dry days.",
    ),
    FeedPost(
        id = 9,
        topic = "Books",
        title = "The shelf by the laundromat",
        summary = "A leave-one, take-one bookshelf gets a new collection every wash cycle.",
        byline = "Jon Bell · Apr 10",
        body = "Paperbacks share the shelf with picture books, a dictionary and a stack of sheet music. The owner " +
            "straightens it at closing and moves the heavier books to the bottom.\n\n" +
            "Leave a note inside the cover if you want to tell the next reader where you found it.",
    ),
    FeedPost(
        id = 10,
        topic = "Food",
        title = "Dinner from the market ends",
        summary = "The Saturday market winds down with a small pile of imperfect produce.",
        byline = "Maya Chen · Apr 9",
        body = "In the last half hour, the growers sort what is left into shallow crates. A few bruised tomatoes or " +
            "undersized carrots often cost less and travel home just fine.\n\n" +
            "What is available changes each week, so bring a bag and plan supper after you get there.",
    ),
)
