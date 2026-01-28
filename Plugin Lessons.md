Microbot Plugin Development Guide: Lessons Learned
1. Project Structure
A standard Microbot plugin requires three specific files to function correctly within the RuneLite environment:
Config.java: Defines user settings (e.g., Tree name, Power chop toggle).
Plugin.java: The entry point. It handles dependency injection (@Inject), startup, and shutdown logic. It must use the @PluginDescriptor annotation to appear in the side panel.
Script.java: Contains the logic loop. Extends Script and uses a ScheduledExecutorService to run tasks periodically.
2. API Changes & Deprecations
During development, we discovered that the API is evolving. Old methods found in older documentation are no longer stable.
Deprecated: Rs2GameObject (static methods like Rs2GameObject.interact).
The New Way: Accessing the Tile Object Cache.
The Reliable Way: Instead of using the "Query Builder" (which was missing methods like .name() in your version), we found that using Java Streams is the most robust method.
Pattern:
code
Java
// Access the cache -> Get Stream -> Filter with standard Java -> Find First/Min
Microbot.getRs2TileObjectCache().getStream()
    .filter(obj -> obj.getName().equalsIgnoreCase("Name"))
    .findFirst()
    .orElse(null);
3. Thread Safety (The "Interrupted" Error)
The most critical error encountered was:
java.lang.RuntimeException: Interrupted waiting for client thread
The Cause
The game runs on a Client Thread. Your script runs on a separate Background Thread.
If your background thread asks the game for data (e.g., obj.getName()) too many times in a loop, it spams the game engine with requests. The engine eventually chokes, interrupts the connection, and crashes the script.
The Solution
You must move the "Search" logic onto the Client Thread using invoke.
Incorrect (Crashes):
code
Java
// Logic running on Background Thread
Rs2TileObjectModel tree = findClosestTree(); // Calls .getName() internally
if (tree != null) tree.click();
Correct (Stable):
code
Java
// Move the heavy lifting to the Game Thread
Microbot.getClientThread().invoke(() -> {
    // This block runs INSTANTLY inside the game loop.
    // It is safe to check .getName() 100 times here.
    Rs2TileObjectModel tree = findClosestTree();
    if (tree != null) {
        tree.click("Chop down");
    }
});
4. Common Java Errors in Microbot
AtomicBoolean: Microbot.pauseAllScripts is an AtomicBoolean.
Wrong: if (Microbot.pauseAllScripts) ...
Right: if (Microbot.pauseAllScripts.get()) ...
Private Access: You cannot access fields like x.name directly on Inventory items.
Right: Use the getter method: x.getName().
Input == Null: This Run/Build error usually indicates a missing icon.png in the resources folder. It is annoying but doesn't stop the code from working.
5. The Golden Logic Template
For future gathering scripts (Mining, Fishing, etc.), use this template to ensure stability:
code
Java
public boolean run(Config config) {
    mainScheduledFuture = scheduledExecutorService.scheduleWithFixedDelay(() -> {
        try {
            // 1. Safety Checks (Login, Pause, Animation)
            if (!Microbot.isLoggedIn() || Microbot.pauseAllScripts.get() || Rs2Player.isAnimating()) return;

            // 2. Inventory Check (Drop or Bank)
            if (Rs2Inventory.isFull()) {
                handleInventory();
                return;
            }

            // 3. Find & Interact (Safely on Client Thread)
            Microbot.getClientThread().invoke(() -> {
                Rs2TileObjectModel target = Microbot.getRs2TileObjectCache().getStream()
                    .filter(x -> x.getName().equals(config.targetName()))
                    .min(Comparator.comparingInt(x -> x.getWorldLocation().distanceTo(Rs2Player.getWorldLocation())))
                    .orElse(null);

                if (target != null) {
                    target.click(config.action());
                }
            });

            // 4. Sleep (On Script Thread) to prevent spam-clicking
            sleep(1500, 2000);

        } catch (Exception e) {
            System.out.println("Error: " + e.getMessage());
        }
    }, 0, 1000, TimeUnit.MILLISECONDS);
    return true;
}