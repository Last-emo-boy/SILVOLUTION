// Simulation models for the Gowin DSP multipliers as yosys's dsp_map.v instantiates them:
// all internal registers off (CLK/CE tied to 0), ASIGN/BSIGN select signedness.
// Only the combinational product is modelled; that is exactly what synth_gowin emits.
module MULT9X9(A, SIA, B, SIB, ASIGN, BSIGN, ASEL, BSEL, CE, CLK, RESET, DOUT, SOA, SOB);
  input  [8:0] A, SIA, B, SIB; input ASIGN, BSIGN, ASEL, BSEL, CE, CLK, RESET;
  output [17:0] DOUT; output [8:0] SOA, SOB;
  parameter AREG = 1'b0, BREG = 1'b0, OUT_REG = 1'b0, PIPE_REG = 1'b0, ASIGN_REG = 1'b0, BSIGN_REG = 1'b0, SOA_REG = 1'b0;
  parameter MULT_RESET_MODE = "SYNC";
  wire [8:0] a = ASEL ? SIA : A, b = BSEL ? SIB : B;
  wire signed [9:0] as = ASIGN ? {a[8], a} : {1'b0, a};
  wire signed [9:0] bs = BSIGN ? {b[8], b} : {1'b0, b};
  wire signed [19:0] p = as * bs;
  assign DOUT = p[17:0]; assign SOA = a; assign SOB = b;
endmodule

module MULT18X18(A, SIA, B, SIB, ASIGN, BSIGN, ASEL, BSEL, CE, CLK, RESET, DOUT, SOA, SOB);
  input  [17:0] A, SIA, B, SIB; input ASIGN, BSIGN, ASEL, BSEL, CE, CLK, RESET;
  output [35:0] DOUT; output [17:0] SOA, SOB;
  parameter AREG = 1'b0, BREG = 1'b0, OUT_REG = 1'b0, PIPE_REG = 1'b0, ASIGN_REG = 1'b0, BSIGN_REG = 1'b0, SOA_REG = 1'b0;
  parameter MULT_RESET_MODE = "SYNC";
  wire [17:0] a = ASEL ? SIA : A, b = BSEL ? SIB : B;
  wire signed [18:0] as = ASIGN ? {a[17], a} : {1'b0, a};
  wire signed [18:0] bs = BSIGN ? {b[17], b} : {1'b0, b};
  wire signed [37:0] p = as * bs;
  assign DOUT = p[35:0]; assign SOA = a; assign SOB = b;
endmodule
