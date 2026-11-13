//! `inv add sword 3 add shield 1 remove sword 1` -> prints the report. Exists so tests/cli.rs can test a real program.
use windmill_testkit::Inventory;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut inv = Inventory::new(10);
    for op in args.chunks(3) {
        let (Some(cmd), Some(item), Some(n)) = (op.first(), op.get(1), op.get(2).and_then(|n| n.parse().ok())) else {
            eprintln!("usage: inv (add|remove) <item> <count> ...");
            std::process::exit(2);
        };
        let r = if cmd == "add" { inv.add(item, n) } else { inv.remove(item, n) };
        if let Err(e) = r {
            eprintln!("error: {e:?}");
            std::process::exit(1);
        }
    }
    println!("{}", inv.report());
}
