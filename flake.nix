{
  description = "SWE-Pruner MCP server for context-aware code pruning";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSystem = f: nixpkgs.lib.genAttrs systems f;
    in
    {
      packages = forEachSystem (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.python312Packages.buildPythonApplication rec {
            pname = "swe-pruner-mcp";
            version = "0.1.0";
            format = "pyproject";

            src = self;

            build-system = with pkgs.python312Packages; [
              hatchling
            ];

            dependencies = with pkgs.python312Packages; [
              mcp
              torch
              transformers
              huggingface-hub
              pydantic
            ];

            nativeBuildInputs = [ pkgs.makeWrapper ];
            propagatedBuildInputs = [ pkgs.ripgrep ];

            pythonImportsCheck = [ "swe_pruner_mcp.server" ];

            postFixup = ''
              wrapProgram "$out/bin/swe-pruner-mcp" \
                --prefix PATH : "${pkgs.lib.makeBinPath [ pkgs.ripgrep ]}"
            '';

            meta = with pkgs.lib; {
              description = "SWE-Pruner MCP server for context-aware code pruning";
              homepage = "https://github.com/Ayanami1314/swe-pruner";
              license = licenses.mit;
              mainProgram = "swe-pruner-mcp";
            };
          };
        }
      );

      devShells = forEachSystem (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python312
              pkgs.python312Packages.uv
              pkgs.ripgrep
            ];
          };
        }
      );
    };
}
