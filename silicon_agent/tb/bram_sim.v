// Simulation models for block RAM primitives as yosys's memory mapping instantiates them.
// They model: 16 kbit storage, per-port data width (1/2/4/9/18) with the 14-bit address whose low bits
// are don't-care for wide ports, clock enable, write enable, synchronous read (NOREG = one register),
// READBEFOREWRITE / NORMAL / WRITETHROUGH write modes, and sync/async reset of the output register.
// Memory starts at zero (yosys emits init no_undef).

// ---------------------------------------------------------------- Lattice ECP5 DP16KD
module DP16KD(
  input DIA17, DIA16, DIA15, DIA14, DIA13, DIA12, DIA11, DIA10, DIA9, DIA8, DIA7, DIA6, DIA5, DIA4, DIA3, DIA2, DIA1, DIA0,
  input ADA13, ADA12, ADA11, ADA10, ADA9, ADA8, ADA7, ADA6, ADA5, ADA4, ADA3, ADA2, ADA1, ADA0,
  input CEA, OCEA, CLKA, WEA, RSTA, input CSA2, CSA1, CSA0,
  output reg DOA17, DOA16, DOA15, DOA14, DOA13, DOA12, DOA11, DOA10, DOA9, DOA8, DOA7, DOA6, DOA5, DOA4, DOA3, DOA2, DOA1, DOA0,
  input DIB17, DIB16, DIB15, DIB14, DIB13, DIB12, DIB11, DIB10, DIB9, DIB8, DIB7, DIB6, DIB5, DIB4, DIB3, DIB2, DIB1, DIB0,
  input ADB13, ADB12, ADB11, ADB10, ADB9, ADB8, ADB7, ADB6, ADB5, ADB4, ADB3, ADB2, ADB1, ADB0,
  input CEB, OCEB, CLKB, WEB, RSTB, input CSB2, CSB1, CSB0,
  output reg DOB17, DOB16, DOB15, DOB14, DOB13, DOB12, DOB11, DOB10, DOB9, DOB8, DOB7, DOB6, DOB5, DOB4, DOB3, DOB2, DOB1, DOB0
);
  parameter DATA_WIDTH_A = 18, DATA_WIDTH_B = 18;
  parameter REGMODE_A = "NOREG", REGMODE_B = "NOREG";
  parameter RESETMODE = "SYNC", ASYNC_RESET_RELEASE = "SYNC";
  parameter CSDECODE_A = "0b000", CSDECODE_B = "0b000";
  parameter WRITEMODE_A = "NORMAL", WRITEMODE_B = "NORMAL";
  parameter GSR = "ENABLED", INIT_DATA = "STATIC", CLKAMUX = "CLKA", CLKBMUX = "CLKB";
  parameter CEAMUX = "CEA", CEBMUX = "CEB", OCEAMUX = "OCEA", OCEBMUX = "OCEB", WEAMUX = "WEA", WEBMUX = "WEB", RSTAMUX = "RSTA", RSTBMUX = "RSTB";
  parameter DIA17MUX="DIA17", DIA16MUX="DIA16", DIA15MUX="DIA15", DIA14MUX="DIA14", DIA13MUX="DIA13", DIA12MUX="DIA12", DIA11MUX="DIA11", DIA10MUX="DIA10", DIA9MUX="DIA9", DIA8MUX="DIA8", DIA7MUX="DIA7", DIA6MUX="DIA6", DIA5MUX="DIA5", DIA4MUX="DIA4", DIA3MUX="DIA3", DIA2MUX="DIA2", DIA1MUX="DIA1", DIA0MUX="DIA0";
  parameter DIB17MUX="DIB17", DIB16MUX="DIB16", DIB15MUX="DIB15", DIB14MUX="DIB14", DIB13MUX="DIB13", DIB12MUX="DIB12", DIB11MUX="DIB11", DIB10MUX="DIB10", DIB9MUX="DIB9", DIB8MUX="DIB8", DIB7MUX="DIB7", DIB6MUX="DIB6", DIB5MUX="DIB5", DIB4MUX="DIB4", DIB3MUX="DIB3", DIB2MUX="DIB2", DIB1MUX="DIB1", DIB0MUX="DIB0";
  parameter ADA13MUX="ADA13", ADA12MUX="ADA12", ADA11MUX="ADA11", ADA10MUX="ADA10", ADA9MUX="ADA9", ADA8MUX="ADA8", ADA7MUX="ADA7", ADA6MUX="ADA6", ADA5MUX="ADA5", ADA4MUX="ADA4", ADA3MUX="ADA3", ADA2MUX="ADA2", ADA1MUX="ADA1", ADA0MUX="ADA0";
  parameter ADB13MUX="ADB13", ADB12MUX="ADB12", ADB11MUX="ADB11", ADB10MUX="ADB10", ADB9MUX="ADB9", ADB8MUX="ADB8", ADB7MUX="ADB7", ADB6MUX="ADB6", ADB5MUX="ADB5", ADB4MUX="ADB4", ADB3MUX="ADB3", ADB2MUX="ADB2", ADB1MUX="ADB1", ADB0MUX="ADB0";
  parameter CSA2MUX="CSA2", CSA1MUX="CSA1", CSA0MUX="CSA0", CSB2MUX="CSB2", CSB1MUX="CSB1", CSB0MUX="CSB0";
  parameter INITVAL_00 = 320'h0, INITVAL_01 = 320'h0, INITVAL_02 = 320'h0, INITVAL_03 = 320'h0, INITVAL_04 = 320'h0, INITVAL_05 = 320'h0, INITVAL_06 = 320'h0, INITVAL_07 = 320'h0;
  parameter INITVAL_08 = 320'h0, INITVAL_09 = 320'h0, INITVAL_0A = 320'h0, INITVAL_0B = 320'h0, INITVAL_0C = 320'h0, INITVAL_0D = 320'h0, INITVAL_0E = 320'h0, INITVAL_0F = 320'h0;
  parameter INITVAL_10 = 320'h0, INITVAL_11 = 320'h0, INITVAL_12 = 320'h0, INITVAL_13 = 320'h0, INITVAL_14 = 320'h0, INITVAL_15 = 320'h0, INITVAL_16 = 320'h0, INITVAL_17 = 320'h0;
  parameter INITVAL_18 = 320'h0, INITVAL_19 = 320'h0, INITVAL_1A = 320'h0, INITVAL_1B = 320'h0, INITVAL_1C = 320'h0, INITVAL_1D = 320'h0, INITVAL_1E = 320'h0, INITVAL_1F = 320'h0;
  parameter INITVAL_20 = 320'h0, INITVAL_21 = 320'h0, INITVAL_22 = 320'h0, INITVAL_23 = 320'h0, INITVAL_24 = 320'h0, INITVAL_25 = 320'h0, INITVAL_26 = 320'h0, INITVAL_27 = 320'h0;
  parameter INITVAL_28 = 320'h0, INITVAL_29 = 320'h0, INITVAL_2A = 320'h0, INITVAL_2B = 320'h0, INITVAL_2C = 320'h0, INITVAL_2D = 320'h0, INITVAL_2E = 320'h0, INITVAL_2F = 320'h0;
  parameter INITVAL_30 = 320'h0, INITVAL_31 = 320'h0, INITVAL_32 = 320'h0, INITVAL_33 = 320'h0, INITVAL_34 = 320'h0, INITVAL_35 = 320'h0, INITVAL_36 = 320'h0, INITVAL_37 = 320'h0;
  parameter INITVAL_38 = 320'h0, INITVAL_39 = 320'h0, INITVAL_3A = 320'h0, INITVAL_3B = 320'h0, INITVAL_3C = 320'h0, INITVAL_3D = 320'h0, INITVAL_3E = 320'h0, INITVAL_3F = 320'h0;

  // 18 kbit as 2048 x 9 (byte lanes); a port of width w covers w/9 lanes (w<9: within one lane)
  reg [8:0] mem [0:2047];
  integer i;
  // INITVAL_xx: 16 slots of 20 bits (18 data bits each) per parameter, 64 parameters = 1024 x 18 = the whole array;
  // slot k of INITVAL_xx is word (xx*16 + k) of the 18-bit view (lanes 2w, 2w+1). A ROM (yosys memory_bram with
  // init) relies on this; a RAM starts at zero as before.
  reg [319:0] iv [0:63];
  initial begin
    iv[0]=INITVAL_00; iv[1]=INITVAL_01; iv[2]=INITVAL_02; iv[3]=INITVAL_03; iv[4]=INITVAL_04; iv[5]=INITVAL_05; iv[6]=INITVAL_06; iv[7]=INITVAL_07;
    iv[8]=INITVAL_08; iv[9]=INITVAL_09; iv[10]=INITVAL_0A; iv[11]=INITVAL_0B; iv[12]=INITVAL_0C; iv[13]=INITVAL_0D; iv[14]=INITVAL_0E; iv[15]=INITVAL_0F;
    iv[16]=INITVAL_10; iv[17]=INITVAL_11; iv[18]=INITVAL_12; iv[19]=INITVAL_13; iv[20]=INITVAL_14; iv[21]=INITVAL_15; iv[22]=INITVAL_16; iv[23]=INITVAL_17;
    iv[24]=INITVAL_18; iv[25]=INITVAL_19; iv[26]=INITVAL_1A; iv[27]=INITVAL_1B; iv[28]=INITVAL_1C; iv[29]=INITVAL_1D; iv[30]=INITVAL_1E; iv[31]=INITVAL_1F;
    iv[32]=INITVAL_20; iv[33]=INITVAL_21; iv[34]=INITVAL_22; iv[35]=INITVAL_23; iv[36]=INITVAL_24; iv[37]=INITVAL_25; iv[38]=INITVAL_26; iv[39]=INITVAL_27;
    iv[40]=INITVAL_28; iv[41]=INITVAL_29; iv[42]=INITVAL_2A; iv[43]=INITVAL_2B; iv[44]=INITVAL_2C; iv[45]=INITVAL_2D; iv[46]=INITVAL_2E; iv[47]=INITVAL_2F;
    iv[48]=INITVAL_30; iv[49]=INITVAL_31; iv[50]=INITVAL_32; iv[51]=INITVAL_33; iv[52]=INITVAL_34; iv[53]=INITVAL_35; iv[54]=INITVAL_36; iv[55]=INITVAL_37;
    iv[56]=INITVAL_38; iv[57]=INITVAL_39; iv[58]=INITVAL_3A; iv[59]=INITVAL_3B; iv[60]=INITVAL_3C; iv[61]=INITVAL_3D; iv[62]=INITVAL_3E; iv[63]=INITVAL_3F;
    for (i = 0; i < 1024; i = i + 1) begin
      mem[2*i]   = iv[i / 16][(i % 16) * 20 +: 9];
      mem[2*i+1] = iv[i / 16][(i % 16) * 20 + 9 +: 9];
    end
  end

  wire [17:0] dia = {DIA17,DIA16,DIA15,DIA14,DIA13,DIA12,DIA11,DIA10,DIA9,DIA8,DIA7,DIA6,DIA5,DIA4,DIA3,DIA2,DIA1,DIA0};
  wire [17:0] dib = {DIB17,DIB16,DIB15,DIB14,DIB13,DIB12,DIB11,DIB10,DIB9,DIB8,DIB7,DIB6,DIB5,DIB4,DIB3,DIB2,DIB1,DIB0};
  wire [13:0] ada = {ADA13,ADA12,ADA11,ADA10,ADA9,ADA8,ADA7,ADA6,ADA5,ADA4,ADA3,ADA2,ADA1,ADA0};
  wire [13:0] adb = {ADB13,ADB12,ADB11,ADB10,ADB9,ADB8,ADB7,ADB6,ADB5,ADB4,ADB3,ADB2,ADB1,ADB0};
  reg  [17:0] doa, dob;
  always @* {DOA17,DOA16,DOA15,DOA14,DOA13,DOA12,DOA11,DOA10,DOA9,DOA8,DOA7,DOA6,DOA5,DOA4,DOA3,DOA2,DOA1,DOA0} = doa;
  always @* {DOB17,DOB16,DOB15,DOB14,DOB13,DOB12,DOB11,DOB10,DOB9,DOB8,DOB7,DOB6,DOB5,DOB4,DOB3,DOB2,DOB1,DOB0} = dob;

  // address -> (lane index, bit offset) for a given width; the 14-bit address is bit-granular
  function [17:0] rd; input [13:0] ad; input integer w; integer lane, off;
    begin
      if (w >= 18) begin lane = ad[13:4] * 2; rd = {mem[lane+1], mem[lane]}; end
      else if (w == 9) begin lane = ad[13:3]; rd = {9'd0, mem[lane]}; end
      else begin lane = ad[13:3]; off = ad[2:0]; rd = (mem[lane] >> off) & ((1 << w) - 1); end
    end
  endfunction
  task wr; input [13:0] ad; input integer w; input [17:0] d; integer lane, off, k;
    begin
      if (w >= 18) begin lane = ad[13:4] * 2; mem[lane] = d[8:0]; mem[lane+1] = d[17:9]; end
      else if (w == 9) begin lane = ad[13:3]; mem[lane] = d[8:0]; end
      else begin lane = ad[13:3]; off = ad[2:0]; for (k = 0; k < w; k = k + 1) mem[lane][off+k] = d[k]; end
    end
  endtask

  // 36-bit pseudo-dual-port mode (DATA_WIDTH 36): the two ports form one wide port -- A writes {DIB,DIA} at ADA
  // (36-bit words, address bits [13:5]), B reads {DOB,DOA} at ADB
  wire wide = (DATA_WIDTH_A == 36) || (DATA_WIDTH_B == 36);
  task wr36; input [13:0] ad; input [35:0] d; integer b; begin b = ad[13:5] * 4; mem[b] = d[8:0]; mem[b+1] = d[17:9]; mem[b+2] = d[26:18]; mem[b+3] = d[35:27]; end endtask
  function [35:0] rd36; input [13:0] ad; integer b; begin b = ad[13:5] * 4; rd36 = {mem[b+3], mem[b+2], mem[b+1], mem[b]}; end endfunction
  always @(posedge CLKA) begin
    if (CEA) begin
      if (wide) begin
        if (WEA) wr36(ada, {dib, dia});
      end else if (WEA) begin
        if (WRITEMODE_A == "READBEFOREWRITE") doa <= rd(ada, DATA_WIDTH_A);
        wr(ada, DATA_WIDTH_A, dia);
        if (WRITEMODE_A == "WRITETHROUGH") doa <= dia;
      end else doa <= rd(ada, DATA_WIDTH_A);
      if (RSTA && RESETMODE == "SYNC") doa <= 18'd0;
    end
  end
  always @(posedge CLKB) begin
    if (CEB) begin
      if (wide) begin
        {dob, doa} <= rd36(adb);
      end else if (WEB) begin
        if (WRITEMODE_B == "READBEFOREWRITE") dob <= rd(adb, DATA_WIDTH_B);
        wr(adb, DATA_WIDTH_B, dib);
        if (WRITEMODE_B == "WRITETHROUGH") dob <= dib;
      end else dob <= rd(adb, DATA_WIDTH_B);
      if (RSTB && RESETMODE == "SYNC") dob <= 18'd0;
    end
  end
  always @(posedge RSTA) if (RESETMODE == "ASYNC") doa <= 18'd0;
  always @(posedge RSTB) if (RESETMODE == "ASYNC") dob <= 18'd0;
  initial begin doa = 18'd0; dob = 18'd0; end
endmodule

// ---------------------------------------------------------------- Gowin DPB / DPX9B / SDPB (GW1N)
// 16 kbit (DPB) or 18 kbit with x9 lanes (DPX9B); yosys drives width-dependent 14-bit addresses the same way.
module DPX9B(CLKA, CEA, CLKB, CEB, OCEA, OCEB, RESETA, RESETB, WREA, WREB, ADA, ADB, DIA, DIB, BLKSELA, BLKSELB, DOA, DOB);
  input CLKA, CEA, CLKB, CEB, OCEA, OCEB, RESETA, RESETB, WREA, WREB; input [13:0] ADA, ADB; input [17:0] DIA, DIB; input [2:0] BLKSELA, BLKSELB;
  output reg [17:0] DOA, DOB;
  parameter READ_MODE0 = 1'b0, READ_MODE1 = 1'b0, WRITE_MODE0 = 2'b00, WRITE_MODE1 = 2'b00, BIT_WIDTH_0 = 18, BIT_WIDTH_1 = 18, BLK_SEL_0 = 3'b000, BLK_SEL_1 = 3'b000, RESET_MODE = "SYNC";
  parameter INIT_RAM_00=288'h0, INIT_RAM_01=288'h0, INIT_RAM_02=288'h0, INIT_RAM_03=288'h0, INIT_RAM_04=288'h0, INIT_RAM_05=288'h0, INIT_RAM_06=288'h0, INIT_RAM_07=288'h0, INIT_RAM_08=288'h0, INIT_RAM_09=288'h0, INIT_RAM_0A=288'h0, INIT_RAM_0B=288'h0, INIT_RAM_0C=288'h0, INIT_RAM_0D=288'h0, INIT_RAM_0E=288'h0, INIT_RAM_0F=288'h0;
  parameter INIT_RAM_10=288'h0, INIT_RAM_11=288'h0, INIT_RAM_12=288'h0, INIT_RAM_13=288'h0, INIT_RAM_14=288'h0, INIT_RAM_15=288'h0, INIT_RAM_16=288'h0, INIT_RAM_17=288'h0, INIT_RAM_18=288'h0, INIT_RAM_19=288'h0, INIT_RAM_1A=288'h0, INIT_RAM_1B=288'h0, INIT_RAM_1C=288'h0, INIT_RAM_1D=288'h0, INIT_RAM_1E=288'h0, INIT_RAM_1F=288'h0;
  parameter INIT_RAM_20=288'h0, INIT_RAM_21=288'h0, INIT_RAM_22=288'h0, INIT_RAM_23=288'h0, INIT_RAM_24=288'h0, INIT_RAM_25=288'h0, INIT_RAM_26=288'h0, INIT_RAM_27=288'h0, INIT_RAM_28=288'h0, INIT_RAM_29=288'h0, INIT_RAM_2A=288'h0, INIT_RAM_2B=288'h0, INIT_RAM_2C=288'h0, INIT_RAM_2D=288'h0, INIT_RAM_2E=288'h0, INIT_RAM_2F=288'h0;
  parameter INIT_RAM_30=288'h0, INIT_RAM_31=288'h0, INIT_RAM_32=288'h0, INIT_RAM_33=288'h0, INIT_RAM_34=288'h0, INIT_RAM_35=288'h0, INIT_RAM_36=288'h0, INIT_RAM_37=288'h0, INIT_RAM_38=288'h0, INIT_RAM_39=288'h0, INIT_RAM_3A=288'h0, INIT_RAM_3B=288'h0, INIT_RAM_3C=288'h0, INIT_RAM_3D=288'h0, INIT_RAM_3E=288'h0, INIT_RAM_3F=288'h0;
  reg [8:0] mem [0:2047];
  integer i; initial begin for (i = 0; i < 2048; i = i + 1) mem[i] = 9'd0; DOA = 0; DOB = 0; end
  // INIT_RAM_xx: 16 words of 18 bits per parameter (yosys packs a ROM's contents this way): word i lives at
  // lanes 2i, 2i+1 of the 18-bit view
  reg [287:0] gw_iv [0:63];
  initial begin
    gw_iv[0]=INIT_RAM_00;
    gw_iv[1]=INIT_RAM_01;
    gw_iv[2]=INIT_RAM_02;
    gw_iv[3]=INIT_RAM_03;
    gw_iv[4]=INIT_RAM_04;
    gw_iv[5]=INIT_RAM_05;
    gw_iv[6]=INIT_RAM_06;
    gw_iv[7]=INIT_RAM_07;
    gw_iv[8]=INIT_RAM_08;
    gw_iv[9]=INIT_RAM_09;
    gw_iv[10]=INIT_RAM_0A;
    gw_iv[11]=INIT_RAM_0B;
    gw_iv[12]=INIT_RAM_0C;
    gw_iv[13]=INIT_RAM_0D;
    gw_iv[14]=INIT_RAM_0E;
    gw_iv[15]=INIT_RAM_0F;
    gw_iv[16]=INIT_RAM_10;
    gw_iv[17]=INIT_RAM_11;
    gw_iv[18]=INIT_RAM_12;
    gw_iv[19]=INIT_RAM_13;
    gw_iv[20]=INIT_RAM_14;
    gw_iv[21]=INIT_RAM_15;
    gw_iv[22]=INIT_RAM_16;
    gw_iv[23]=INIT_RAM_17;
    gw_iv[24]=INIT_RAM_18;
    gw_iv[25]=INIT_RAM_19;
    gw_iv[26]=INIT_RAM_1A;
    gw_iv[27]=INIT_RAM_1B;
    gw_iv[28]=INIT_RAM_1C;
    gw_iv[29]=INIT_RAM_1D;
    gw_iv[30]=INIT_RAM_1E;
    gw_iv[31]=INIT_RAM_1F;
    gw_iv[32]=INIT_RAM_20;
    gw_iv[33]=INIT_RAM_21;
    gw_iv[34]=INIT_RAM_22;
    gw_iv[35]=INIT_RAM_23;
    gw_iv[36]=INIT_RAM_24;
    gw_iv[37]=INIT_RAM_25;
    gw_iv[38]=INIT_RAM_26;
    gw_iv[39]=INIT_RAM_27;
    gw_iv[40]=INIT_RAM_28;
    gw_iv[41]=INIT_RAM_29;
    gw_iv[42]=INIT_RAM_2A;
    gw_iv[43]=INIT_RAM_2B;
    gw_iv[44]=INIT_RAM_2C;
    gw_iv[45]=INIT_RAM_2D;
    gw_iv[46]=INIT_RAM_2E;
    gw_iv[47]=INIT_RAM_2F;
    gw_iv[48]=INIT_RAM_30;
    gw_iv[49]=INIT_RAM_31;
    gw_iv[50]=INIT_RAM_32;
    gw_iv[51]=INIT_RAM_33;
    gw_iv[52]=INIT_RAM_34;
    gw_iv[53]=INIT_RAM_35;
    gw_iv[54]=INIT_RAM_36;
    gw_iv[55]=INIT_RAM_37;
    gw_iv[56]=INIT_RAM_38;
    gw_iv[57]=INIT_RAM_39;
    gw_iv[58]=INIT_RAM_3A;
    gw_iv[59]=INIT_RAM_3B;
    gw_iv[60]=INIT_RAM_3C;
    gw_iv[61]=INIT_RAM_3D;
    gw_iv[62]=INIT_RAM_3E;
    gw_iv[63]=INIT_RAM_3F;
    for (i = 0; i < 1024; i = i + 1) begin
      mem[2*i]   = gw_iv[i / 16][(i % 16) * 18 +: 9];
      mem[2*i+1] = gw_iv[i / 16][(i % 16) * 18 + 9 +: 9];
    end
  end
  function [17:0] rd; input [13:0] ad; input integer w; integer lane, off;
    begin
      if (w >= 18) begin lane = ad[13:4] * 2; rd = {mem[lane+1], mem[lane]}; end
      else if (w == 9) begin lane = ad[13:3]; rd = {9'd0, mem[lane]}; end
      else begin lane = ad[13:3]; off = ad[2:0]; rd = (mem[lane] >> off) & ((1 << w) - 1); end
    end
  endfunction
  task wr; input [13:0] ad; input integer w; input [17:0] d; integer lane, off, k;
    begin
      if (w >= 18) begin lane = ad[13:4] * 2; mem[lane] = d[8:0]; mem[lane+1] = d[17:9]; end
      else if (w == 9) begin lane = ad[13:3]; mem[lane] = d[8:0]; end
      else begin lane = ad[13:3]; off = ad[2:0]; for (k = 0; k < w; k = k + 1) mem[lane][off+k] = d[k]; end
    end
  endtask
  always @(posedge CLKA) if (CEA) begin
    if (RESETA && RESET_MODE == "SYNC") DOA <= 0;
    else if (WREA) begin
      if (WRITE_MODE0 == 2'b10) DOA <= rd(ADA, BIT_WIDTH_0);
      wr(ADA, BIT_WIDTH_0, DIA);
      if (WRITE_MODE0 == 2'b01) DOA <= DIA;
    end else DOA <= rd(ADA, BIT_WIDTH_0);
  end
  always @(posedge CLKB) if (CEB) begin
    if (RESETB && RESET_MODE == "SYNC") DOB <= 0;
    else if (WREB) begin
      if (WRITE_MODE1 == 2'b10) DOB <= rd(ADB, BIT_WIDTH_1);
      wr(ADB, BIT_WIDTH_1, DIB);
      if (WRITE_MODE1 == 2'b01) DOB <= DIB;
    end else DOB <= rd(ADB, BIT_WIDTH_1);
  end
endmodule

module DPB(CLKA, CEA, CLKB, CEB, OCEA, OCEB, RESETA, RESETB, WREA, WREB, ADA, ADB, BLKSELA, BLKSELB, DIA, DIB, DOA, DOB);
  input CLKA, CEA, CLKB, CEB, OCEA, OCEB, RESETA, RESETB, WREA, WREB; input [13:0] ADA, ADB; input [15:0] DIA, DIB; input [2:0] BLKSELA, BLKSELB;
  output [15:0] DOA, DOB;
  parameter READ_MODE0 = 1'b0, READ_MODE1 = 1'b0, WRITE_MODE0 = 2'b00, WRITE_MODE1 = 2'b00, BIT_WIDTH_0 = 16, BIT_WIDTH_1 = 16, BLK_SEL_0 = 3'b000, BLK_SEL_1 = 3'b000, RESET_MODE = "SYNC";
  parameter INIT_RAM_00=256'h0, INIT_RAM_01=256'h0, INIT_RAM_02=256'h0, INIT_RAM_03=256'h0, INIT_RAM_04=256'h0, INIT_RAM_05=256'h0, INIT_RAM_06=256'h0, INIT_RAM_07=256'h0, INIT_RAM_08=256'h0, INIT_RAM_09=256'h0, INIT_RAM_0A=256'h0, INIT_RAM_0B=256'h0, INIT_RAM_0C=256'h0, INIT_RAM_0D=256'h0, INIT_RAM_0E=256'h0, INIT_RAM_0F=256'h0;
  parameter INIT_RAM_10=256'h0, INIT_RAM_11=256'h0, INIT_RAM_12=256'h0, INIT_RAM_13=256'h0, INIT_RAM_14=256'h0, INIT_RAM_15=256'h0, INIT_RAM_16=256'h0, INIT_RAM_17=256'h0, INIT_RAM_18=256'h0, INIT_RAM_19=256'h0, INIT_RAM_1A=256'h0, INIT_RAM_1B=256'h0, INIT_RAM_1C=256'h0, INIT_RAM_1D=256'h0, INIT_RAM_1E=256'h0, INIT_RAM_1F=256'h0;
  parameter INIT_RAM_20=256'h0, INIT_RAM_21=256'h0, INIT_RAM_22=256'h0, INIT_RAM_23=256'h0, INIT_RAM_24=256'h0, INIT_RAM_25=256'h0, INIT_RAM_26=256'h0, INIT_RAM_27=256'h0, INIT_RAM_28=256'h0, INIT_RAM_29=256'h0, INIT_RAM_2A=256'h0, INIT_RAM_2B=256'h0, INIT_RAM_2C=256'h0, INIT_RAM_2D=256'h0, INIT_RAM_2E=256'h0, INIT_RAM_2F=256'h0;
  parameter INIT_RAM_30=256'h0, INIT_RAM_31=256'h0, INIT_RAM_32=256'h0, INIT_RAM_33=256'h0, INIT_RAM_34=256'h0, INIT_RAM_35=256'h0, INIT_RAM_36=256'h0, INIT_RAM_37=256'h0, INIT_RAM_38=256'h0, INIT_RAM_39=256'h0, INIT_RAM_3A=256'h0, INIT_RAM_3B=256'h0, INIT_RAM_3C=256'h0, INIT_RAM_3D=256'h0, INIT_RAM_3E=256'h0, INIT_RAM_3F=256'h0;
  // x8 lanes: yosys packs 8-bit lanes into the 16-bit ports (x8_wr_data / x8_rd_data); model as 9-bit lanes with bit 8 unused
  wire [17:0] doa18, dob18;
  DPX9B #(.READ_MODE0(READ_MODE0), .READ_MODE1(READ_MODE1), .WRITE_MODE0(WRITE_MODE0), .WRITE_MODE1(WRITE_MODE1),
          .BIT_WIDTH_0(BIT_WIDTH_0 / 8 * 9 + BIT_WIDTH_0 % 8), .BIT_WIDTH_1(BIT_WIDTH_1 / 8 * 9 + BIT_WIDTH_1 % 8), .RESET_MODE(RESET_MODE))
    u (.CLKA(CLKA), .CEA(CEA), .CLKB(CLKB), .CEB(CEB), .OCEA(OCEA), .OCEB(OCEB), .RESETA(RESETA), .RESETB(RESETB), .WREA(WREA), .WREB(WREB),
       .ADA(ADA), .ADB(ADB), .DIA({1'b0, DIA[15:8], 1'b0, DIA[7:0]}), .DIB({1'b0, DIB[15:8], 1'b0, DIB[7:0]}), .BLKSELA(BLKSELA), .BLKSELB(BLKSELB), .DOA(doa18), .DOB(dob18));
  assign DOA = {doa18[16:9], doa18[7:0]};
  assign DOB = {dob18[16:9], dob18[7:0]};
endmodule


// Gowin single-port SP (16 kbit): yosys uses it when one port both reads and writes. Modelled on 8-bit lanes;
// yosys packs data through x8_wr_data/x8_rd_data so DI/DO bytes are contiguous in the low bits.
module SP (DO, DI, BLKSEL, AD, WRE, CLK, CE, OCE, RESET);
  output [31:0] DO; input [31:0] DI; input [2:0] BLKSEL; input [13:0] AD; input WRE, CLK, CE, OCE, RESET;
  parameter READ_MODE = 1'b0, WRITE_MODE = 2'b00, BIT_WIDTH = 32, BLK_SEL = 3'b000, RESET_MODE = "SYNC";
  parameter INIT_RAM_00=256'h0, INIT_RAM_01=256'h0, INIT_RAM_02=256'h0, INIT_RAM_03=256'h0, INIT_RAM_04=256'h0, INIT_RAM_05=256'h0, INIT_RAM_06=256'h0, INIT_RAM_07=256'h0, INIT_RAM_08=256'h0, INIT_RAM_09=256'h0, INIT_RAM_0A=256'h0, INIT_RAM_0B=256'h0, INIT_RAM_0C=256'h0, INIT_RAM_0D=256'h0, INIT_RAM_0E=256'h0, INIT_RAM_0F=256'h0;
  parameter INIT_RAM_10=256'h0, INIT_RAM_11=256'h0, INIT_RAM_12=256'h0, INIT_RAM_13=256'h0, INIT_RAM_14=256'h0, INIT_RAM_15=256'h0, INIT_RAM_16=256'h0, INIT_RAM_17=256'h0, INIT_RAM_18=256'h0, INIT_RAM_19=256'h0, INIT_RAM_1A=256'h0, INIT_RAM_1B=256'h0, INIT_RAM_1C=256'h0, INIT_RAM_1D=256'h0, INIT_RAM_1E=256'h0, INIT_RAM_1F=256'h0;
  parameter INIT_RAM_20=256'h0, INIT_RAM_21=256'h0, INIT_RAM_22=256'h0, INIT_RAM_23=256'h0, INIT_RAM_24=256'h0, INIT_RAM_25=256'h0, INIT_RAM_26=256'h0, INIT_RAM_27=256'h0, INIT_RAM_28=256'h0, INIT_RAM_29=256'h0, INIT_RAM_2A=256'h0, INIT_RAM_2B=256'h0, INIT_RAM_2C=256'h0, INIT_RAM_2D=256'h0, INIT_RAM_2E=256'h0, INIT_RAM_2F=256'h0;
  parameter INIT_RAM_30=256'h0, INIT_RAM_31=256'h0, INIT_RAM_32=256'h0, INIT_RAM_33=256'h0, INIT_RAM_34=256'h0, INIT_RAM_35=256'h0, INIT_RAM_36=256'h0, INIT_RAM_37=256'h0, INIT_RAM_38=256'h0, INIT_RAM_39=256'h0, INIT_RAM_3A=256'h0, INIT_RAM_3B=256'h0, INIT_RAM_3C=256'h0, INIT_RAM_3D=256'h0, INIT_RAM_3E=256'h0, INIT_RAM_3F=256'h0;
  reg [7:0] mem [0:2047];
  integer i; reg [31:0] dq;
  // INIT_RAM_xx (x8 view): 32 lanes of 8 bits per parameter (yosys init_slice_x8); lane i = INIT_RAM_[i/32][(i%32)*8 +: 8]
  reg [255:0] gw_iv [0:63];
  initial begin
    gw_iv[0]=INIT_RAM_00;
    gw_iv[1]=INIT_RAM_01;
    gw_iv[2]=INIT_RAM_02;
    gw_iv[3]=INIT_RAM_03;
    gw_iv[4]=INIT_RAM_04;
    gw_iv[5]=INIT_RAM_05;
    gw_iv[6]=INIT_RAM_06;
    gw_iv[7]=INIT_RAM_07;
    gw_iv[8]=INIT_RAM_08;
    gw_iv[9]=INIT_RAM_09;
    gw_iv[10]=INIT_RAM_0A;
    gw_iv[11]=INIT_RAM_0B;
    gw_iv[12]=INIT_RAM_0C;
    gw_iv[13]=INIT_RAM_0D;
    gw_iv[14]=INIT_RAM_0E;
    gw_iv[15]=INIT_RAM_0F;
    gw_iv[16]=INIT_RAM_10;
    gw_iv[17]=INIT_RAM_11;
    gw_iv[18]=INIT_RAM_12;
    gw_iv[19]=INIT_RAM_13;
    gw_iv[20]=INIT_RAM_14;
    gw_iv[21]=INIT_RAM_15;
    gw_iv[22]=INIT_RAM_16;
    gw_iv[23]=INIT_RAM_17;
    gw_iv[24]=INIT_RAM_18;
    gw_iv[25]=INIT_RAM_19;
    gw_iv[26]=INIT_RAM_1A;
    gw_iv[27]=INIT_RAM_1B;
    gw_iv[28]=INIT_RAM_1C;
    gw_iv[29]=INIT_RAM_1D;
    gw_iv[30]=INIT_RAM_1E;
    gw_iv[31]=INIT_RAM_1F;
    gw_iv[32]=INIT_RAM_20;
    gw_iv[33]=INIT_RAM_21;
    gw_iv[34]=INIT_RAM_22;
    gw_iv[35]=INIT_RAM_23;
    gw_iv[36]=INIT_RAM_24;
    gw_iv[37]=INIT_RAM_25;
    gw_iv[38]=INIT_RAM_26;
    gw_iv[39]=INIT_RAM_27;
    gw_iv[40]=INIT_RAM_28;
    gw_iv[41]=INIT_RAM_29;
    gw_iv[42]=INIT_RAM_2A;
    gw_iv[43]=INIT_RAM_2B;
    gw_iv[44]=INIT_RAM_2C;
    gw_iv[45]=INIT_RAM_2D;
    gw_iv[46]=INIT_RAM_2E;
    gw_iv[47]=INIT_RAM_2F;
    gw_iv[48]=INIT_RAM_30;
    gw_iv[49]=INIT_RAM_31;
    gw_iv[50]=INIT_RAM_32;
    gw_iv[51]=INIT_RAM_33;
    gw_iv[52]=INIT_RAM_34;
    gw_iv[53]=INIT_RAM_35;
    gw_iv[54]=INIT_RAM_36;
    gw_iv[55]=INIT_RAM_37;
    gw_iv[56]=INIT_RAM_38;
    gw_iv[57]=INIT_RAM_39;
    gw_iv[58]=INIT_RAM_3A;
    gw_iv[59]=INIT_RAM_3B;
    gw_iv[60]=INIT_RAM_3C;
    gw_iv[61]=INIT_RAM_3D;
    gw_iv[62]=INIT_RAM_3E;
    gw_iv[63]=INIT_RAM_3F;
    for (i = 0; i < 2048; i = i + 1) mem[i] = gw_iv[i / 32][(i % 32) * 8 +: 8];
  end
  initial begin for (i = 0; i < 2048; i = i + 1) mem[i] = 8'd0; dq = 0; end
  function [31:0] rd; input [13:0] ad; input integer w; integer b, off;
    begin
      if (w >= 32) begin b = ad[13:5] * 4; rd = {mem[b+3], mem[b+2], mem[b+1], mem[b]}; end
      else if (w == 16) begin b = ad[13:4] * 2; rd = {16'd0, mem[b+1], mem[b]}; end
      else if (w == 8) begin b = ad[13:3]; rd = {24'd0, mem[b]}; end
      else begin b = ad[13:3]; off = ad[2:0]; rd = (mem[b] >> off) & ((1 << w) - 1); end
    end
  endfunction
  task wr; input [13:0] ad; input integer w; input [31:0] d; integer b, off, k;
    begin
      if (w >= 32) begin b = ad[13:5] * 4; {mem[b+3], mem[b+2], mem[b+1], mem[b]} = d; end
      else if (w == 16) begin b = ad[13:4] * 2; mem[b] = d[7:0]; mem[b+1] = d[15:8]; end
      else if (w == 8) begin b = ad[13:3]; mem[b] = d[7:0]; end
      else begin b = ad[13:3]; off = ad[2:0]; for (k = 0; k < w; k = k + 1) mem[b][off+k] = d[k]; end
    end
  endtask
  always @(posedge CLK) if (CE) begin
    if (RESET && RESET_MODE == "SYNC") dq <= 0;
    else if (WRE) begin
      if (WRITE_MODE == 2'b10) dq <= rd(AD, BIT_WIDTH);
      wr(AD, BIT_WIDTH, DI);
      if (WRITE_MODE == 2'b01) dq <= DI;
    end else dq <= rd(AD, BIT_WIDTH);
  end
  assign DO = dq;
endmodule

// Gowin single-port SPX9 (18 kbit, x9 lanes, 36-bit data pins)
module SPX9 (DO, DI, BLKSEL, AD, WRE, CLK, CE, OCE, RESET);
  output [35:0] DO; input [35:0] DI; input [2:0] BLKSEL; input [13:0] AD; input WRE, CLK, CE, OCE, RESET;
  parameter READ_MODE = 1'b0, WRITE_MODE = 2'b00, BIT_WIDTH = 36, BLK_SEL = 3'b000, RESET_MODE = "SYNC";
  parameter INIT_RAM_00=288'h0, INIT_RAM_01=288'h0, INIT_RAM_02=288'h0, INIT_RAM_03=288'h0, INIT_RAM_04=288'h0, INIT_RAM_05=288'h0, INIT_RAM_06=288'h0, INIT_RAM_07=288'h0, INIT_RAM_08=288'h0, INIT_RAM_09=288'h0, INIT_RAM_0A=288'h0, INIT_RAM_0B=288'h0, INIT_RAM_0C=288'h0, INIT_RAM_0D=288'h0, INIT_RAM_0E=288'h0, INIT_RAM_0F=288'h0;
  parameter INIT_RAM_10=288'h0, INIT_RAM_11=288'h0, INIT_RAM_12=288'h0, INIT_RAM_13=288'h0, INIT_RAM_14=288'h0, INIT_RAM_15=288'h0, INIT_RAM_16=288'h0, INIT_RAM_17=288'h0, INIT_RAM_18=288'h0, INIT_RAM_19=288'h0, INIT_RAM_1A=288'h0, INIT_RAM_1B=288'h0, INIT_RAM_1C=288'h0, INIT_RAM_1D=288'h0, INIT_RAM_1E=288'h0, INIT_RAM_1F=288'h0;
  parameter INIT_RAM_20=288'h0, INIT_RAM_21=288'h0, INIT_RAM_22=288'h0, INIT_RAM_23=288'h0, INIT_RAM_24=288'h0, INIT_RAM_25=288'h0, INIT_RAM_26=288'h0, INIT_RAM_27=288'h0, INIT_RAM_28=288'h0, INIT_RAM_29=288'h0, INIT_RAM_2A=288'h0, INIT_RAM_2B=288'h0, INIT_RAM_2C=288'h0, INIT_RAM_2D=288'h0, INIT_RAM_2E=288'h0, INIT_RAM_2F=288'h0;
  parameter INIT_RAM_30=288'h0, INIT_RAM_31=288'h0, INIT_RAM_32=288'h0, INIT_RAM_33=288'h0, INIT_RAM_34=288'h0, INIT_RAM_35=288'h0, INIT_RAM_36=288'h0, INIT_RAM_37=288'h0, INIT_RAM_38=288'h0, INIT_RAM_39=288'h0, INIT_RAM_3A=288'h0, INIT_RAM_3B=288'h0, INIT_RAM_3C=288'h0, INIT_RAM_3D=288'h0, INIT_RAM_3E=288'h0, INIT_RAM_3F=288'h0;
  reg [8:0] mem [0:2047];
  integer i; reg [35:0] dq;
  // INIT_RAM_xx: 16 words of 18 bits per parameter (yosys packs a ROM's contents this way): word i lives at
  // lanes 2i, 2i+1 of the 18-bit view
  reg [287:0] gw_iv [0:63];
  initial begin
    gw_iv[0]=INIT_RAM_00;
    gw_iv[1]=INIT_RAM_01;
    gw_iv[2]=INIT_RAM_02;
    gw_iv[3]=INIT_RAM_03;
    gw_iv[4]=INIT_RAM_04;
    gw_iv[5]=INIT_RAM_05;
    gw_iv[6]=INIT_RAM_06;
    gw_iv[7]=INIT_RAM_07;
    gw_iv[8]=INIT_RAM_08;
    gw_iv[9]=INIT_RAM_09;
    gw_iv[10]=INIT_RAM_0A;
    gw_iv[11]=INIT_RAM_0B;
    gw_iv[12]=INIT_RAM_0C;
    gw_iv[13]=INIT_RAM_0D;
    gw_iv[14]=INIT_RAM_0E;
    gw_iv[15]=INIT_RAM_0F;
    gw_iv[16]=INIT_RAM_10;
    gw_iv[17]=INIT_RAM_11;
    gw_iv[18]=INIT_RAM_12;
    gw_iv[19]=INIT_RAM_13;
    gw_iv[20]=INIT_RAM_14;
    gw_iv[21]=INIT_RAM_15;
    gw_iv[22]=INIT_RAM_16;
    gw_iv[23]=INIT_RAM_17;
    gw_iv[24]=INIT_RAM_18;
    gw_iv[25]=INIT_RAM_19;
    gw_iv[26]=INIT_RAM_1A;
    gw_iv[27]=INIT_RAM_1B;
    gw_iv[28]=INIT_RAM_1C;
    gw_iv[29]=INIT_RAM_1D;
    gw_iv[30]=INIT_RAM_1E;
    gw_iv[31]=INIT_RAM_1F;
    gw_iv[32]=INIT_RAM_20;
    gw_iv[33]=INIT_RAM_21;
    gw_iv[34]=INIT_RAM_22;
    gw_iv[35]=INIT_RAM_23;
    gw_iv[36]=INIT_RAM_24;
    gw_iv[37]=INIT_RAM_25;
    gw_iv[38]=INIT_RAM_26;
    gw_iv[39]=INIT_RAM_27;
    gw_iv[40]=INIT_RAM_28;
    gw_iv[41]=INIT_RAM_29;
    gw_iv[42]=INIT_RAM_2A;
    gw_iv[43]=INIT_RAM_2B;
    gw_iv[44]=INIT_RAM_2C;
    gw_iv[45]=INIT_RAM_2D;
    gw_iv[46]=INIT_RAM_2E;
    gw_iv[47]=INIT_RAM_2F;
    gw_iv[48]=INIT_RAM_30;
    gw_iv[49]=INIT_RAM_31;
    gw_iv[50]=INIT_RAM_32;
    gw_iv[51]=INIT_RAM_33;
    gw_iv[52]=INIT_RAM_34;
    gw_iv[53]=INIT_RAM_35;
    gw_iv[54]=INIT_RAM_36;
    gw_iv[55]=INIT_RAM_37;
    gw_iv[56]=INIT_RAM_38;
    gw_iv[57]=INIT_RAM_39;
    gw_iv[58]=INIT_RAM_3A;
    gw_iv[59]=INIT_RAM_3B;
    gw_iv[60]=INIT_RAM_3C;
    gw_iv[61]=INIT_RAM_3D;
    gw_iv[62]=INIT_RAM_3E;
    gw_iv[63]=INIT_RAM_3F;
    for (i = 0; i < 1024; i = i + 1) begin
      mem[2*i]   = gw_iv[i / 16][(i % 16) * 18 +: 9];
      mem[2*i+1] = gw_iv[i / 16][(i % 16) * 18 + 9 +: 9];
    end
  end
  initial begin for (i = 0; i < 2048; i = i + 1) mem[i] = 9'd0; dq = 0; end
  function [35:0] rd; input [13:0] ad; input integer w; integer b, off;
    begin
      if (w >= 36) begin b = ad[13:5] * 4; rd = {mem[b+3], mem[b+2], mem[b+1], mem[b]}; end
      else if (w == 18) begin b = ad[13:4] * 2; rd = {18'd0, mem[b+1], mem[b]}; end
      else if (w == 9) begin b = ad[13:3]; rd = {27'd0, mem[b]}; end
      else begin b = ad[13:3]; off = ad[2:0]; rd = (mem[b] >> off) & ((1 << w) - 1); end
    end
  endfunction
  task wr; input [13:0] ad; input integer w; input [35:0] d; integer b, off, k;
    begin
      if (w >= 36) begin b = ad[13:5] * 4; {mem[b+3], mem[b+2], mem[b+1], mem[b]} = d; end
      else if (w == 18) begin b = ad[13:4] * 2; mem[b] = d[8:0]; mem[b+1] = d[17:9]; end
      else if (w == 9) begin b = ad[13:3]; mem[b] = d[8:0]; end
      else begin b = ad[13:3]; off = ad[2:0]; for (k = 0; k < w; k = k + 1) mem[b][off+k] = d[k]; end
    end
  endtask
  always @(posedge CLK) if (CE) begin
    if (RESET && RESET_MODE == "SYNC") dq <= 0;
    else if (WRE) begin
      if (WRITE_MODE == 2'b10) dq <= rd(AD, BIT_WIDTH);
      wr(AD, BIT_WIDTH, DI);
      if (WRITE_MODE == 2'b01) dq <= DI;
    end else dq <= rd(AD, BIT_WIDTH);
  end
  assign DO = dq;
endmodule

// Gowin simple-dual-port SDPX9B (18 kbit, x9 lanes): port A writes (CEA = write enable), port B reads (CEB = read clock enable).
module SDPX9B(CLKA, CEA, CLKB, CEB, OCE, RESETA, RESETB, ADA, ADB, BLKSELA, BLKSELB, DI, DO);
  input CLKA, CEA, CLKB, CEB, OCE, RESETA, RESETB; input [13:0] ADA, ADB; input [2:0] BLKSELA, BLKSELB; input [35:0] DI; output reg [35:0] DO;
  parameter READ_MODE = 1'b0, BIT_WIDTH_0 = 36, BIT_WIDTH_1 = 36, BLK_SEL_0 = 3'b000, BLK_SEL_1 = 3'b000, RESET_MODE = "SYNC";
  parameter INIT_RAM_00=288'h0, INIT_RAM_01=288'h0, INIT_RAM_02=288'h0, INIT_RAM_03=288'h0, INIT_RAM_04=288'h0, INIT_RAM_05=288'h0, INIT_RAM_06=288'h0, INIT_RAM_07=288'h0, INIT_RAM_08=288'h0, INIT_RAM_09=288'h0, INIT_RAM_0A=288'h0, INIT_RAM_0B=288'h0, INIT_RAM_0C=288'h0, INIT_RAM_0D=288'h0, INIT_RAM_0E=288'h0, INIT_RAM_0F=288'h0;
  parameter INIT_RAM_10=288'h0, INIT_RAM_11=288'h0, INIT_RAM_12=288'h0, INIT_RAM_13=288'h0, INIT_RAM_14=288'h0, INIT_RAM_15=288'h0, INIT_RAM_16=288'h0, INIT_RAM_17=288'h0, INIT_RAM_18=288'h0, INIT_RAM_19=288'h0, INIT_RAM_1A=288'h0, INIT_RAM_1B=288'h0, INIT_RAM_1C=288'h0, INIT_RAM_1D=288'h0, INIT_RAM_1E=288'h0, INIT_RAM_1F=288'h0;
  parameter INIT_RAM_20=288'h0, INIT_RAM_21=288'h0, INIT_RAM_22=288'h0, INIT_RAM_23=288'h0, INIT_RAM_24=288'h0, INIT_RAM_25=288'h0, INIT_RAM_26=288'h0, INIT_RAM_27=288'h0, INIT_RAM_28=288'h0, INIT_RAM_29=288'h0, INIT_RAM_2A=288'h0, INIT_RAM_2B=288'h0, INIT_RAM_2C=288'h0, INIT_RAM_2D=288'h0, INIT_RAM_2E=288'h0, INIT_RAM_2F=288'h0;
  parameter INIT_RAM_30=288'h0, INIT_RAM_31=288'h0, INIT_RAM_32=288'h0, INIT_RAM_33=288'h0, INIT_RAM_34=288'h0, INIT_RAM_35=288'h0, INIT_RAM_36=288'h0, INIT_RAM_37=288'h0, INIT_RAM_38=288'h0, INIT_RAM_39=288'h0, INIT_RAM_3A=288'h0, INIT_RAM_3B=288'h0, INIT_RAM_3C=288'h0, INIT_RAM_3D=288'h0, INIT_RAM_3E=288'h0, INIT_RAM_3F=288'h0;
  reg [8:0] mem [0:2047];
  integer i; initial begin for (i = 0; i < 2048; i = i + 1) mem[i] = 9'd0; DO = 0; end
  // INIT_RAM_xx: 16 words of 18 bits per parameter (yosys packs a ROM's contents this way): word i lives at
  // lanes 2i, 2i+1 of the 18-bit view
  reg [287:0] gw_iv [0:63];
  initial begin
    gw_iv[0]=INIT_RAM_00;
    gw_iv[1]=INIT_RAM_01;
    gw_iv[2]=INIT_RAM_02;
    gw_iv[3]=INIT_RAM_03;
    gw_iv[4]=INIT_RAM_04;
    gw_iv[5]=INIT_RAM_05;
    gw_iv[6]=INIT_RAM_06;
    gw_iv[7]=INIT_RAM_07;
    gw_iv[8]=INIT_RAM_08;
    gw_iv[9]=INIT_RAM_09;
    gw_iv[10]=INIT_RAM_0A;
    gw_iv[11]=INIT_RAM_0B;
    gw_iv[12]=INIT_RAM_0C;
    gw_iv[13]=INIT_RAM_0D;
    gw_iv[14]=INIT_RAM_0E;
    gw_iv[15]=INIT_RAM_0F;
    gw_iv[16]=INIT_RAM_10;
    gw_iv[17]=INIT_RAM_11;
    gw_iv[18]=INIT_RAM_12;
    gw_iv[19]=INIT_RAM_13;
    gw_iv[20]=INIT_RAM_14;
    gw_iv[21]=INIT_RAM_15;
    gw_iv[22]=INIT_RAM_16;
    gw_iv[23]=INIT_RAM_17;
    gw_iv[24]=INIT_RAM_18;
    gw_iv[25]=INIT_RAM_19;
    gw_iv[26]=INIT_RAM_1A;
    gw_iv[27]=INIT_RAM_1B;
    gw_iv[28]=INIT_RAM_1C;
    gw_iv[29]=INIT_RAM_1D;
    gw_iv[30]=INIT_RAM_1E;
    gw_iv[31]=INIT_RAM_1F;
    gw_iv[32]=INIT_RAM_20;
    gw_iv[33]=INIT_RAM_21;
    gw_iv[34]=INIT_RAM_22;
    gw_iv[35]=INIT_RAM_23;
    gw_iv[36]=INIT_RAM_24;
    gw_iv[37]=INIT_RAM_25;
    gw_iv[38]=INIT_RAM_26;
    gw_iv[39]=INIT_RAM_27;
    gw_iv[40]=INIT_RAM_28;
    gw_iv[41]=INIT_RAM_29;
    gw_iv[42]=INIT_RAM_2A;
    gw_iv[43]=INIT_RAM_2B;
    gw_iv[44]=INIT_RAM_2C;
    gw_iv[45]=INIT_RAM_2D;
    gw_iv[46]=INIT_RAM_2E;
    gw_iv[47]=INIT_RAM_2F;
    gw_iv[48]=INIT_RAM_30;
    gw_iv[49]=INIT_RAM_31;
    gw_iv[50]=INIT_RAM_32;
    gw_iv[51]=INIT_RAM_33;
    gw_iv[52]=INIT_RAM_34;
    gw_iv[53]=INIT_RAM_35;
    gw_iv[54]=INIT_RAM_36;
    gw_iv[55]=INIT_RAM_37;
    gw_iv[56]=INIT_RAM_38;
    gw_iv[57]=INIT_RAM_39;
    gw_iv[58]=INIT_RAM_3A;
    gw_iv[59]=INIT_RAM_3B;
    gw_iv[60]=INIT_RAM_3C;
    gw_iv[61]=INIT_RAM_3D;
    gw_iv[62]=INIT_RAM_3E;
    gw_iv[63]=INIT_RAM_3F;
    for (i = 0; i < 1024; i = i + 1) begin
      mem[2*i]   = gw_iv[i / 16][(i % 16) * 18 +: 9];
      mem[2*i+1] = gw_iv[i / 16][(i % 16) * 18 + 9 +: 9];
    end
  end
  function [35:0] rd; input [13:0] ad; input integer w; integer b, off;
    begin
      if (w >= 36) begin b = ad[13:5] * 4; rd = {mem[b+3], mem[b+2], mem[b+1], mem[b]}; end
      else if (w == 18) begin b = ad[13:4] * 2; rd = {18'd0, mem[b+1], mem[b]}; end
      else if (w == 9) begin b = ad[13:3]; rd = {27'd0, mem[b]}; end
      else begin b = ad[13:3]; off = ad[2:0]; rd = (mem[b] >> off) & ((1 << w) - 1); end
    end
  endfunction
  task wr; input [13:0] ad; input integer w; input [35:0] d; integer b, off, k;
    begin
      if (w >= 36) begin b = ad[13:5] * 4; {mem[b+3], mem[b+2], mem[b+1], mem[b]} = d; end
      else if (w == 18) begin b = ad[13:4] * 2; mem[b] = d[8:0]; mem[b+1] = d[17:9]; end
      else if (w == 9) begin b = ad[13:3]; mem[b] = d[8:0]; end
      else begin b = ad[13:3]; off = ad[2:0]; for (k = 0; k < w; k = k + 1) mem[b][off+k] = d[k]; end
    end
  endtask
  always @(posedge CLKA) if (CEA) wr(ADA, BIT_WIDTH_0, DI);
  always @(posedge CLKB) if (CEB) begin
    if (RESETB && RESET_MODE == "SYNC") DO <= 0;
    else DO <= rd(ADB, BIT_WIDTH_1);
  end
endmodule

// SDPB: 16 kbit x8-lane variant; yosys packs data through x8_wr_data / x8_rd_data
module SDPB(CLKA, CEA, CLKB, CEB, OCE, RESETA, RESETB, ADA, ADB, BLKSELA, BLKSELB, DI, DO);
  input CLKA, CEA, CLKB, CEB, OCE, RESETA, RESETB; input [13:0] ADA, ADB; input [2:0] BLKSELA, BLKSELB; input [31:0] DI; output [31:0] DO;
  parameter READ_MODE = 1'b0, BIT_WIDTH_0 = 32, BIT_WIDTH_1 = 32, BLK_SEL_0 = 3'b000, BLK_SEL_1 = 3'b000, RESET_MODE = "SYNC";
  parameter INIT_RAM_00=256'h0, INIT_RAM_01=256'h0, INIT_RAM_02=256'h0, INIT_RAM_03=256'h0, INIT_RAM_04=256'h0, INIT_RAM_05=256'h0, INIT_RAM_06=256'h0, INIT_RAM_07=256'h0, INIT_RAM_08=256'h0, INIT_RAM_09=256'h0, INIT_RAM_0A=256'h0, INIT_RAM_0B=256'h0, INIT_RAM_0C=256'h0, INIT_RAM_0D=256'h0, INIT_RAM_0E=256'h0, INIT_RAM_0F=256'h0;
  parameter INIT_RAM_10=256'h0, INIT_RAM_11=256'h0, INIT_RAM_12=256'h0, INIT_RAM_13=256'h0, INIT_RAM_14=256'h0, INIT_RAM_15=256'h0, INIT_RAM_16=256'h0, INIT_RAM_17=256'h0, INIT_RAM_18=256'h0, INIT_RAM_19=256'h0, INIT_RAM_1A=256'h0, INIT_RAM_1B=256'h0, INIT_RAM_1C=256'h0, INIT_RAM_1D=256'h0, INIT_RAM_1E=256'h0, INIT_RAM_1F=256'h0;
  parameter INIT_RAM_20=256'h0, INIT_RAM_21=256'h0, INIT_RAM_22=256'h0, INIT_RAM_23=256'h0, INIT_RAM_24=256'h0, INIT_RAM_25=256'h0, INIT_RAM_26=256'h0, INIT_RAM_27=256'h0, INIT_RAM_28=256'h0, INIT_RAM_29=256'h0, INIT_RAM_2A=256'h0, INIT_RAM_2B=256'h0, INIT_RAM_2C=256'h0, INIT_RAM_2D=256'h0, INIT_RAM_2E=256'h0, INIT_RAM_2F=256'h0;
  parameter INIT_RAM_30=256'h0, INIT_RAM_31=256'h0, INIT_RAM_32=256'h0, INIT_RAM_33=256'h0, INIT_RAM_34=256'h0, INIT_RAM_35=256'h0, INIT_RAM_36=256'h0, INIT_RAM_37=256'h0, INIT_RAM_38=256'h0, INIT_RAM_39=256'h0, INIT_RAM_3A=256'h0, INIT_RAM_3B=256'h0, INIT_RAM_3C=256'h0, INIT_RAM_3D=256'h0, INIT_RAM_3E=256'h0, INIT_RAM_3F=256'h0;
  wire [35:0] do36;
  SDPX9B #(.READ_MODE(READ_MODE), .BIT_WIDTH_0(BIT_WIDTH_0 / 8 * 9 + BIT_WIDTH_0 % 8), .BIT_WIDTH_1(BIT_WIDTH_1 / 8 * 9 + BIT_WIDTH_1 % 8), .RESET_MODE(RESET_MODE))
    u (.CLKA(CLKA), .CEA(CEA), .CLKB(CLKB), .CEB(CEB), .OCE(OCE), .RESETA(RESETA), .RESETB(RESETB), .ADA(ADA), .ADB(ADB), .BLKSELA(BLKSELA), .BLKSELB(BLKSELB),
       .DI({1'b0, DI[31:24], 1'b0, DI[23:16], 1'b0, DI[15:8], 1'b0, DI[7:0]}), .DO(do36));
  assign DO = {do36[34:27], do36[25:18], do36[16:9], do36[7:0]};
endmodule
